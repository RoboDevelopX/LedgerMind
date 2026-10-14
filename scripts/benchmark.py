"""Benchmark Nemotron Nano, Super, Ultra (and Lightning) on Ledgermind's three kinds of work, with real calls.

    python scripts/benchmark.py              # needs NEBIUS_API_KEY; writes docs/benchmark.md
    python scripts/benchmark.py --runs 5     # more repetitions for steadier medians
    python scripts/benchmark.py --mock       # dry run of the script itself; numbers are simulated, nothing is written

Tasks (synthetic inputs, no personal data):
  categorise  20 merchants -> categories as JSON (the job routed to Nano); score = accuracy against labels
  tool choice 6 questions with all tools offered (the job routed to Super); score = right first tool
  narrate     explain a what-if result in 3 sentences (the job routed to Ultra); score = number guard passes
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ledgermind.agent.numguard import check  # noqa: E402
from ledgermind.agent.prompts import SYSTEM_PROMPT  # noqa: E402
from ledgermind.config import ModelConfig, Settings, get_settings, missing_key_help  # noqa: E402
from ledgermind.llm import ChatRequest, LLMClient, LLMError, MockClient, TokenFactoryClient  # noqa: E402
from ledgermind.llm.jsonutil import parse_json  # noqa: E402
from ledgermind.llm.mock import mock_brain  # noqa: E402
from ledgermind.llm.router import call_cost  # noqa: E402
from ledgermind.merchants import CATEGORIES  # noqa: E402
from ledgermind.skills import tools  # noqa: E402
from ledgermind.skills.categorize import CATEGORIZE_PROMPT  # noqa: E402

MODELS = ("nano", "lightning", "super", "ultra")

LABELLED = [("Swiggy", "debit", "Dining"), ("BigBasket", "debit", "Groceries"), ("Uber", "debit", "Transport"),
            ("Netflix", "debit", "Subscriptions"), ("Apollo Pharmacy", "debit", "Health"),
            ("IndiGo Airlines", "debit", "Travel"), ("BESCOM Electricity", "debit", "Utilities"),
            ("Croma Electronics", "debit", "Shopping"), ("ACKO General Insurance", "debit", "Insurance"),
            ("PVR Cinemas", "debit", "Entertainment"), ("Coursera", "debit", "Education"),
            ("ACME Corp Payroll", "credit", "Income"), ("Indian Oil Petrol Pump", "debit", "Transport"),
            ("Third Wave Coffee", "debit", "Dining"), ("DMart", "debit", "Groceries"),
            ("Ferns N Petals", "debit", "Gifts"), ("ATM Cash Withdrawal", "debit", "Cash"),
            ("Credit Card Annual Fee", "debit", "Fees"), ("Airtel Postpaid", "debit", "Utilities"),
            ("Zepto", "debit", "Groceries")]

TOOL_QUESTIONS = [("How much did I spend on groceries last month?", "spending_summary"),
                  ("Do I have any subscriptions I forgot about?", "detect_subscriptions"),
                  ("Was there anything odd on my account recently?", "detect_anomalies"),
                  ("What happens if I spend 20% less on shopping?", "what_if_simulator"),
                  ("I want to save 2 lakh for a bike by June next year", "set_goal"),
                  ("Limit my dining to 8000 a month", "set_budget")]

WHAT_IF = {"horizon_months": 12, "changes": [{"category": "Dining", "change_pct": -30, "current_monthly": "₹18,684",
                                              "new_monthly": "₹13,079", "monthly_difference": "₹5,605"}],
           "monthly_saving_change": "₹5,605", "total_change_over_horizon": "₹67,262",
           "surplus_before": "₹33,131", "surplus_after": "₹38,736",
           "goals": [{"name": "Japan trip", "remaining": "₹3,00,000", "months_to_goal_before": 10,
                      "months_to_goal_after": 8, "months_sooner": 2}],
           "assumption": "averages of the last full months repeat; nothing else changes"}


def categorise(client: LLMClient, model: ModelConfig) -> tuple[Any, float]:
    listing = "\n".join(f"m{i}|{name}|{direction}" for i, (name, direction, _) in enumerate(LABELLED))
    system = CATEGORIZE_PROMPT.format(categories=", ".join(CATEGORIES))
    req = ChatRequest(messages=[{"role": "system", "content": system}, {"role": "user", "content": listing}],
                      task="categorize", json_mode=True, max_tokens=2000, temperature=0.0)
    res = client.chat(model, req)
    try:
        data = parse_json(res.content)
    except ValueError:
        return res, 0.0
    right = sum(isinstance(data, dict) and isinstance(data.get(f"m{i}"), dict)
                and data[f"m{i}"].get("category") == label for i, (_, _, label) in enumerate(LABELLED))
    return res, right / len(LABELLED)


def tool_choice(client: LLMClient, model: ModelConfig, question: str, expected: str) -> tuple[Any, float]:
    system = SYSTEM_PROMPT.format(today=date(2026, 10, 20).isoformat(), currency="INR",
                                  coverage="2025-11-01 to 2026-10-20", memories="(nothing yet)")
    req = ChatRequest(messages=[{"role": "system", "content": system}, {"role": "user", "content": question}],
                      task="agent", tools=tools(), max_tokens=800, temperature=0.0)
    res = client.chat(model, req)
    return res, float(bool(res.tool_calls) and res.tool_calls[0].name == expected)


def narrate(client: LLMClient, model: ModelConfig) -> tuple[Any, float]:
    req = ChatRequest(messages=[
        {"role": "system", "content": "You explain a personal-finance what-if result. Use only numbers that appear "
                                      "in the data, copied exactly. Three sentences. Not financial advice."},
        {"role": "user", "content": "The user asked: what if I cut dining by 30%?\nDATA:\n"
                                    + json.dumps(WHAT_IF, ensure_ascii=False)}],
        task="deep_answer", max_tokens=1500, temperature=0.3)
    res = client.chat(model, req)
    return res, float(bool(res.content) and check(res.content, WHAT_IF, "30%").ok)


def run(settings: Settings, client: LLMClient, runs: int) -> list[dict[str, Any]]:
    rows = []
    for alias in MODELS:
        model = settings.models[alias]
        for task in ("categorise", "tool choice", "narrate"):
            samples: list[tuple[Any, float]] = []
            errors = 0
            jobs: list[tuple[Any, tuple[Any, ...]]] = (
                [(categorise, (client, model))] if task == "categorise" else
                [(tool_choice, (client, model, q, e)) for q, e in TOOL_QUESTIONS] if task == "tool choice" else
                [(narrate, (client, model))])
            for _ in range(runs):
                for fn, fn_args in jobs:
                    try:
                        samples.append(fn(*fn_args))
                    except LLMError as exc:
                        errors += 1
                        print(f"  {alias} {task}: {exc}", file=sys.stderr)
            if not samples:
                rows.append({"model": model.label, "id": model.id, "task": task, "calls": 0, "errors": errors})
                continue
            results = [r for r, _ in samples]
            rows.append({
                "model": model.label, "id": model.id, "task": task, "calls": len(samples), "errors": errors,
                "median_ms": int(statistics.median(r.latency_ms for r in results)),
                "tokens_in": int(statistics.mean(r.usage.prompt_tokens for r in results)),
                "tokens_out": int(statistics.mean(r.usage.completion_tokens for r in results)),
                "cost": statistics.mean(call_cost(model, r) for r in results),
                "score": statistics.mean(s for _, s in samples),
            })
            print(f"{model.label:<24} {task:<12} {rows[-1]['median_ms']:>6} ms  score {rows[-1]['score']:.0%}")
    return rows


def table(rows: list[dict[str, Any]]) -> str:
    lines = ["| Model | Task | Calls | Median latency | Tokens in / out | Cost per call (USD) | Score |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        if not r["calls"]:
            lines.append(f"| {r['model']} | {r['task']} | 0 | failed ({r['errors']} errors) | | | |")
            continue
        lines.append(f"| {r['model']} | {r['task']} | {r['calls']} | {r['median_ms']:,} ms | "
                     f"{r['tokens_in']:,} / {r['tokens_out']:,} | ${r['cost']:.6f} | {r['score']:.0%} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mock", action="store_true", help="simulated dry run; prints but writes nothing")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "benchmark.md")
    args = ap.parse_args()
    if args.mock:
        os.environ["LEDGERMIND_MOCK"] = "1"
    settings = get_settings()
    if settings.mock and not args.mock:
        print(f"The benchmark needs real calls. {missing_key_help()} (Or use --mock to test the script.)")
        return 1
    client: LLMClient = MockClient(mock_brain) if settings.mock else TokenFactoryClient(settings)
    started = time.monotonic()
    rows = run(settings, client, args.runs)
    md = table(rows)
    print("\n" + md)
    if args.mock:
        print("\n(simulated latencies and scores from the offline mock; not written anywhere)")
        return 0
    thinking = ", ".join(settings.models[a].label for a in MODELS if settings.models[a].thinking) or "none"
    args.out.write_text(
        f"# Nemotron benchmark\n\nMeasured on {date.today().isoformat()} with `python scripts/benchmark.py "
        f"--runs {args.runs}` against Nebius Token Factory ({settings.provider.base_url}), "
        f"{time.monotonic() - started:.0f} s in total. Reasoning (thinking) enabled for: {thinking}; "
        "everything else as configured in `config.yaml`.\n\n" + md + "\n\n"
        "Scores: categorise = share of 20 labelled merchants categorised correctly; tool choice = share of 6 "
        "questions where the first tool call was the right one; narrate = share of explanations whose every "
        "figure passed the number guard.\n", encoding="utf-8")
    print(f"\nWritten to {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
