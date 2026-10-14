"""Periodic consolidation: merge near-duplicates, prune stale low-value memories, and ask Nemotron Nano to
summarise a type that has grown too long. User-written and pinned memories are never pruned or rewritten."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..llm import AllModelsFailed, ChatRequest, ModelRouter
from ..llm.jsonutil import parse_json
from .store import MERGE_SIMILARITY, MemoryStore, similarity

MAX_PER_TYPE = 8
SUMMARY_PROMPT = ("Merge these memories about one person's finances into at most {n} short third-person sentences. "
                  "Keep every distinct fact and copy numbers exactly. "
                  'Reply with JSON only: {{"memories": ["...", ...]}}')


def _protected(m: dict[str, Any]) -> bool:
    return m["pinned"] or m["source"] == "user"


def consolidate(store: MemoryStore, settings: Settings, router: ModelRouter | None = None) -> dict[str, Any]:
    """Run one consolidation pass. Returns what changed."""
    report: dict[str, Any] = {"merged": 0, "pruned": [], "summarised": {}}
    by_type: dict[str, list[dict[str, Any]]] = {}
    for m in store.list():
        by_type.setdefault(m["type"], []).append(m)
    for kind, items in by_type.items():
        keep: list[dict[str, Any]] = []
        for m in sorted(items, key=lambda x: (-x["importance"], x["id"])):
            twin = next((k for k in keep if similarity(k["content"], m["content"]) >= MERGE_SIMILARITY), None)
            if twin is not None and not _protected(m):
                store.db.execute("UPDATE memories SET importance=?, confidence=? WHERE id=?",
                                 [max(twin["importance"], m["importance"]), max(twin["confidence"], m["confidence"]),
                                  twin["id"]])
                store.delete(m["id"])
                report["merged"] += 1
            else:
                keep.append(m)
        by_type[kind] = keep
    now = datetime.now(UTC)
    for m in store.list():
        last = datetime.fromisoformat(m.get("last_used_at") or m["updated_at"])
        if not _protected(m) and m["importance"] < 0.4 and (now - last).days > settings.memory.stale_days:
            store.delete(m["id"])
            report["pruned"].append(m["content"])
    if router is not None:
        for kind in ("habit", "preference", "decision"):
            items = [m for m in store.list(kind) if not _protected(m)]
            if len(items) > MAX_PER_TYPE:
                report["summarised"][kind] = _summarise(store, router, kind, items)
    return report


def _summarise(store: MemoryStore, router: ModelRouter, kind: str, items: list[dict[str, Any]]) -> int:
    req = ChatRequest(messages=[
        {"role": "system", "content": SUMMARY_PROMPT.format(n=MAX_PER_TYPE // 2)},
        {"role": "user", "content": "\n".join(f"- {m['content']}" for m in items)}],
        task="consolidate_memory", json_mode=True, max_tokens=600, temperature=0.0)
    try:
        result, _ = router.chat("fast", req)
        lines = parse_json(result.content).get("memories", [])
    except (AllModelsFailed, ValueError, AttributeError):
        return 0
    lines = [str(x).strip() for x in lines if str(x).strip()][:MAX_PER_TYPE]
    if not lines:
        return 0
    importance = max(m["importance"] for m in items)
    for m in items:
        store.delete(m["id"])
    for line in lines:
        store.upsert(kind, line, importance=importance, source="consolidation")
    return len(lines)
