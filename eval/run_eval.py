"""Run the Ledgermind eval: finance questions over the demo ledger, scored against pandas.

    python eval/run_eval.py            # live Nemotron if NEBIUS_API_KEY is set, otherwise offline mock models
    python eval/run_eval.py --mock     # force offline mode (checks the pipeline, not the model)

For every question the agent's tool calls, the figures in its tool results (the deterministic maths) and the figures
in its final answer (did the model copy them correctly?) are checked against values computed independently with
pandas. A results table is printed and written to eval/results.md.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LEDGERMIND_TODAY", "2026-10-20")
os.environ["LEDGERMIND_PROACTIVE"] = "0"

import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from ledgermind.agent.loop import Agent  # noqa: E402
from ledgermind.agent.numguard import parse_numbers  # noqa: E402
from ledgermind.app import AppState  # noqa: E402
from ledgermind.config import get_settings  # noqa: E402
from ledgermind.ledger import frame  # noqa: E402
from ledgermind.merchants import NON_SPENDING  # noqa: E402
from ledgermind.periods import today  # noqa: E402

TOLERANCE = 0.01   # 1%: lets a model write "₹31.5k" for 31,540, not "₹30k"
GOAL_TARGET, GOAL_DEADLINE, BUDGET = 300_000, date(2027, 3, 31), 10_000


def _spent(df: pd.DataFrame) -> float:
    return float(-df[~df.category.isin(NON_SPENDING)].amount.sum())


def ground_truth(df: pd.DataFrame) -> dict[str, Any]:
    """Expected values, computed from the raw ledger rows with plain pandas."""
    t = today()
    last = (t.replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")
    sep, mtd = df[df.month == last], df[df.month == t.strftime("%Y-%m")]
    by_cat = (-sep[~sep.category.isin(NON_SPENDING)].groupby("category").amount.sum()).sort_values(ascending=False)
    netflix = df[df.merchant == "Netflix"].sort_values("date").amount
    recent = df[df.date >= pd.Timestamp(t) - pd.Timedelta(days=90)]
    debits = recent[(recent.amount < 0) & ~recent.category.isin(NON_SPENDING | {"Housing"})]
    pairs = debits.groupby(["merchant", "date", "amount"]).size()
    duplicate = pairs[pairs >= 2].reset_index()
    full3 = df[df.month.isin(sorted(df.month.unique())[-4:-1])]
    dining_avg = _spent(full3[full3.category == "Dining"]) / 3
    months_left = (GOAL_DEADLINE.year - t.year) * 12 + GOAL_DEADLINE.month - t.month
    return {
        "spent_sep": _spent(sep),
        "income_sep": float(sep[(sep.amount > 0) & (sep.category == "Income")].amount.sum()),
        "dining_sep": _spent(sep[sep.category == "Dining"]),
        "swiggy_sep": _spent(sep[sep.merchant.str.contains("swiggy", case=False)]),
        "top_category_sep": by_cat.index[0],
        "top_category_sep_amount": float(by_cat.iloc[0]),
        "spent_oct_mtd": _spent(mtd),
        "netflix_old": float(-netflix.iloc[0]),
        "netflix_new": float(-netflix.iloc[-1]),
        "duplicate_amazon": float(-duplicate[duplicate.merchant == "Amazon"].amount.iloc[0]),
        "largest_one_off": float(-debits.amount.min()),
        "goal_needed_per_month": GOAL_TARGET / months_left,
        "whatif_dining_30": dining_avg * 0.30,
        "budget_dining": float(BUDGET),
        "dining_oct_mtd": _spent(mtd[mtd.category == "Dining"]),
    }


def has_value(text: str, value: float) -> bool:
    return any(abs(abs(v) - abs(value)) <= max(TOLERANCE * abs(value), 0.6) for _, v in parse_numbers(text))


def score(item: dict[str, Any], out: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    called = [t["name"] for t in out["tools"]]
    tools_ok = all(t in called for t in item.get("tools", []))
    results = json.dumps([out["tool_results_local"].get(t) for t in called], ensure_ascii=False, default=str)
    answer = out.get("reply", "")
    figures = [truth[f] for f in item.get("figures", [])]
    maths_ok = all(has_value(results, v) for v in figures)
    answer_ok = all(has_value(answer, v) for v in figures) and all(
        str(truth.get(x, x)).lower() in answer.lower() for x in item.get("text", []))
    guard_ok = bool(out.get("verified"))
    return {"id": item["id"], "question": item["question"], "called": called, "tools": tools_ok, "maths": maths_ok,
            "answer": answer_ok, "guard": guard_ok, "pass": tools_ok and maths_ok and answer_ok and guard_ok,
            "model": out.get("answer_model") or "-", "seconds": out.get("seconds", 0.0), "reply": answer}


def mark(ok: bool) -> str:
    return "yes" if ok else "NO"


def report(rows: list[dict[str, Any]], mode: str, models: str, memories: list[dict[str, Any]]) -> str:
    passed = sum(r["pass"] for r in rows)
    lines = ["# Ledgermind eval results", "",
             f"- Mode: {mode}", f"- Models: {models}", f"- Ledger: seeded demo, today = {today().isoformat()}",
             f"- **Passed {passed} of {len(rows)} ({passed / len(rows) * 100:.0f}%)**",
             f"- Tool maths matched pandas on {sum(r['maths'] for r in rows)} of {len(rows)} questions; "
             f"answers carried the right figures on {sum(r['answer'] for r in rows)}; "
             f"number guard verified {sum(r['guard'] for r in rows)}.",
             f"- Median time per question: {statistics.median(r['seconds'] for r in rows):.2f} s", "",
             "| # | Question | Tools called | Tools | Maths | Answer | Guard | Pass | Answered by |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['question']} | {', '.join(r['called']) or '-'} | {mark(r['tools'])} | "
                     f"{mark(r['maths'])} | {mark(r['answer'])} | {mark(r['guard'])} | {mark(r['pass'])} | "
                     f"{r['model']} |")
    failures = [r for r in rows if not r["pass"]]
    if failures:
        lines += ["", "## Failures", ""]
        for r in failures:
            lines += [f"**{r['id']}**: {r['question']}", "", "```", r["reply"][:2000], "```", ""]
    lines += ["", "## Memories after the run", "",
              "Source: user = saved by a tool call, chat = extracted by Nano after a turn.", ""]
    lines += [f"- {m['type']} ({m['source']}): {m['content']}" for m in memories] or ["- (none)"]
    return "\n".join(lines) + "\n"


def run_questions(state: AppState, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ask every question against the demo ledger and score the answers."""
    state.load_demo()
    truth = ground_truth(frame(state.db))
    rows = []
    for item in items:
        # A fresh conversation per question, so its figures have to come from a tool rather than an earlier answer.
        # Goals, budgets and memories carry over, which the later questions rely on.
        state.db.execute("DELETE FROM messages")
        started = time.monotonic()
        out = Agent(state).handle(item["question"])
        out["seconds"] = time.monotonic() - started
        row = score(item, out, truth)
        rows.append(row)
        print(f"{'PASS' if row['pass'] else 'FAIL'}  {item['id']:<18} tools={','.join(row['called']) or '-'}"
              f"  maths={mark(row['maths'])} answer={mark(row['answer'])} guard={mark(row['guard'])}"
              f"  {row['seconds']:.1f}s")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mock", action="store_true", help="force offline mock models")
    ap.add_argument("--only", help="run one question id (later questions may depend on earlier ones)")
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "results.md")
    ap.add_argument("--strict", action="store_true", help="exit with an error unless every question passes (CI)")
    args = ap.parse_args()
    if args.mock:
        os.environ["LEDGERMIND_MOCK"] = "1"
    settings = get_settings()
    items = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    if args.only:
        items = [i for i in items if i["id"] == args.only]
    with tempfile.TemporaryDirectory() as tmp:
        state = AppState(settings=settings, db_path=str(Path(tmp) / "eval.db"))
        try:
            rows = run_questions(state, items)
            memories = state.memory.list()
        finally:
            state.db.close()  # Windows cannot delete the temporary folder while SQLite holds the file open
    mode = ("offline mock models: checks routing, maths, privacy and the number guard, not model quality"
            if settings.mock else "live Nemotron on Nebius Token Factory")
    models = ", ".join(f"{role}: {settings.models[chain[0]].label}" for role, chain in settings.roles.items())
    text = report(rows, mode, models, memories)
    args.out.write_text(text, encoding="utf-8")
    passed = sum(r["pass"] for r in rows)
    print(f"\nPassed {passed}/{len(rows)} ({passed / len(rows) * 100:.0f}%) in {mode.split(':')[0]} mode. "
          f"Table written to {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    if args.strict and passed < len(rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
