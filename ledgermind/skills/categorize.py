"""Categorisation: user corrections first, then local rules, then Nemotron Nano in batches.

Only normalised merchant names (redacted by the privacy layer) and a direction (in/out) go to the model.
If the model is unavailable, transactions fall back to "Other" and the app keeps working.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from ..db import Database
from ..ledger import set_category, uncategorized
from ..llm import AllModelsFailed, ChatRequest
from ..llm.jsonutil import parse_json
from ..memory.store import MemoryStore
from ..merchants import CATEGORIES, closest_category, normalise, rule_match
from . import SkillContext, skill

BATCH = 40
CATEGORIZE_PROMPT = (
    "You categorise bank transactions for a personal finance app. Each line is `id|merchant|direction`. "
    "For each id give the best category and a clean merchant name (brand only, no locations or codes).\n"
    "Allowed categories: {categories}.\n"
    'Reply with JSON only: {{"<id>": {{"category": "<category>", "merchant": "<name>"}}, ...}}'
)


def _local(description: str, corrections: dict[str, str]) -> tuple[str, str | None, str]:
    """(merchant, category or None, source) using corrections then rules."""
    merchant = normalise(description)
    if merchant.lower() in corrections:
        return merchant, corrections[merchant.lower()], "correction"
    match = rule_match(description)
    if match:
        return match[0], match[1], "rule"
    return merchant, None, ""


def prepare_rows(db: Database, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill merchant and, where corrections or rules know it, category, before rows are stored."""
    corrections = MemoryStore(db).corrections()
    for r in rows:
        merchant, category, source = _local(r["description"], corrections)
        r["merchant"] = merchant
        if r.get("category_source") != "csv" and category:
            r["category"], r["category_source"] = category, source
    return rows


def categorize_pending(ctx: SkillContext) -> dict[str, int]:
    """Categorise every uncategorised transaction. Returns counts by method."""
    counts: Counter[str] = Counter()
    corrections = MemoryStore(ctx.db).corrections()
    todo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tx in uncategorized(ctx.db):
        merchant, category, source = _local(tx["description"], corrections)
        if category:
            set_category(ctx.db, tx["id"], category, source, merchant)
            counts[source] += 1
        else:
            todo[merchant].append({**tx, "direction": "in" if tx["amount"] > 0 else "out"})
    merchants = list(todo)
    for i in range(0, len(merchants), BATCH):
        labels = _ask_model(ctx, merchants[i:i + BATCH], todo)
        for merchant in merchants[i:i + BATCH]:
            label = labels.get(merchant) or {}
            category = closest_category(str(label.get("category", ""))) if label else None
            clean = str(label.get("merchant") or merchant)[:40] if category else merchant
            for tx in todo[merchant]:
                set_category(ctx.db, tx["id"], category or "Other", "model" if category else "fallback", clean)
                counts["model" if category else "fallback"] += 1
    return dict(counts)


def _ask_model(ctx: SkillContext, merchants: list[str],
               todo: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Ask Nemotron Nano for categories; {} if no model is available or the reply is unusable."""
    if ctx.router is None:
        return {}
    ids = {f"m{i}": m for i, m in enumerate(merchants)}
    listing = "\n".join(f"{key}|{m}|{todo[m][0]['direction']}" for key, m in ids.items())
    req = ChatRequest(messages=[
        {"role": "system", "content": CATEGORIZE_PROMPT.format(categories=", ".join(CATEGORIES))},
        {"role": "user", "content": listing}], task="categorize", json_mode=True, max_tokens=2000, temperature=0.0)
    try:
        result, _ = ctx.router.chat("fast", req)
        data = parse_json(result.content)
    except (AllModelsFailed, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {ids[k]: v for k, v in data.items() if k in ids and isinstance(v, dict)}


@skill("categorize_transactions",
       "Categorise any uncategorised transactions (user corrections first, then rules, then Nemotron Nano).",
       mutates=True)
def categorize_transactions(ctx: SkillContext) -> dict[str, Any]:
    """Run the categoriser over uncategorised rows and report how each was labelled."""
    counts = categorize_pending(ctx)
    return {"categorized": sum(counts.values()), "by_method": counts}


@skill("correct_category",
       "Fix the category for a merchant when the user says it is wrong. Updates past transactions and remembers "
       "the correction so it is never repeated.",
       {"merchant": {"type": "string", "description": "merchant name as shown to the user"},
        "category": {"type": "string", "enum": CATEGORIES}},
       ("merchant", "category"), mutates=True)
def correct_category(ctx: SkillContext, merchant: str, category: str) -> dict[str, Any]:
    """Re-label all of a merchant's transactions and store a `correction` memory."""
    cols = "SELECT id, merchant, category, description FROM transactions WHERE "
    rows = ctx.db.all(cols + "lower(merchant)=lower(?)", [merchant])
    if not rows:
        like = f"%{re.sub(r'[%_]', '', merchant)}%"
        rows = ctx.db.all(cols + "merchant LIKE ? OR description LIKE ?", [like, like])
    if not rows:
        return {"error": f"no transactions found for merchant {merchant!r}"}
    name = Counter(r["merchant"] for r in rows).most_common(1)[0][0]
    before = Counter(r["category"] for r in rows)
    for r in rows:
        set_category(ctx.db, r["id"], category, "user")
    previous = before.most_common(1)[0][0]
    MemoryStore(ctx.db).upsert(
        "correction", f"{name} transactions are {category}" + (f", not {previous}." if previous != category else "."),
        key=name.lower(), confidence=1.0, importance=0.7,
        data={"merchant": name, "category": category,
              "aliases": sorted({normalise(r["description"]).lower() for r in rows} - {name.lower()})},
        source="user")
    return {"merchant": name, "category": category, "transactions_updated": len(rows),
            "previous_categories": dict(before), "remembered": True}
