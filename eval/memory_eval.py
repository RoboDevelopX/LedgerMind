"""Memory-extraction eval: precision and recall of the memories each model extracts from labelled messages.

    python eval/memory_eval.py                    # live (needs NEBIUS_API_KEY): Nano, Lightning, Super
    python eval/memory_eval.py --models nano      # one model
    python eval/memory_eval.py --mock             # offline: scores the rule-based mock, i.e. checks the harness

Each case in eval/memory_cases.yaml is run through the production extractor (`memory.extract.extract_memories`,
including its confidence filter and dedupe) against an empty memory store. A predicted memory is a true positive
when it has the gold type and its text matches the gold regex; each gold memory can be matched once. Negatives
(questions, chatter, facts about other people) must yield nothing; anything extracted there is a false positive.
Results go to eval/memory_results.md.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from ledgermind.config import Settings, get_settings, missing_key_help  # noqa: E402
from ledgermind.db import Database  # noqa: E402
from ledgermind.llm import LLMClient, MockClient, TokenFactoryClient  # noqa: E402
from ledgermind.llm.mock import mock_brain  # noqa: E402
from ledgermind.llm.router import ModelRouter  # noqa: E402
from ledgermind.memory.extract import extract_memories  # noqa: E402
from ledgermind.memory.store import MemoryStore  # noqa: E402

CASES = ROOT / "eval" / "memory_cases.yaml"


def score_case(gold: list[dict[str, str]], predicted: list[dict[str, Any]]) -> tuple[list[dict], int, list[str]]:
    """(matched gold memories, false positives, notes) for one message. Each gold memory matches at most once."""
    unmatched = list(gold)
    matched, fp, notes = [], 0, []
    for p in predicted:
        hit = next((g for g in unmatched if g["type"] == p["type"]
                    and re.search(g["match"], p["content"], re.I)), None)
        if hit:
            unmatched.remove(hit)
            matched.append(hit)
        else:
            fp += 1
            notes.append(f"extra {p['type']}: {p['content']}")
    notes += [f"missed {g['type']} /{g['match']}/" for g in unmatched]
    return matched, fp, notes


def run_model(settings: Settings, client: LLMClient, alias: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    s = settings.model_copy(deep=True)
    s.roles["fast"] = [alias]  # no fallback: we are measuring this model alone
    router = ModelRouter(s, client)
    totals: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = {}
    neg_cases = neg_clean = 0
    latencies, errors, failures = [], 0, []
    for case in cases:
        db = Database(":memory:")
        start = time.monotonic()
        try:
            applied = extract_memories(router, MemoryStore(db), s, case["text"])
        except Exception as exc:  # an eval should report a crash per case, not stop
            errors += 1
            failures.append((case["text"], [f"error: {exc}"]))
            continue
        latencies.append((time.monotonic() - start) * 1000)
        predicted = [a["memory"] for a in applied]
        matched, fp, notes = score_case(case["gold"], predicted)
        totals.update(tp=len(matched), fp=fp, fn=len(case["gold"]) - len(matched))
        for g in case["gold"]:
            by_type.setdefault(g["type"], Counter())["gold"] += 1
        for g in matched:
            by_type[g["type"]]["tp"] += 1
        for p in predicted:
            by_type.setdefault(p["type"], Counter())["pred"] += 1
        if not case["gold"]:
            neg_cases += 1
            neg_clean += fp == 0
        if notes:
            failures.append((case["text"], notes))
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"alias": alias, "label": s.models[alias].label, "precision": precision, "recall": recall, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "negatives_clean": f"{neg_clean}/{neg_cases}",
            "median_ms": int(statistics.median(latencies)) if latencies else 0, "errors": errors,
            "by_type": by_type, "failures": failures}


def report(rows: list[dict[str, Any]], mock: bool, n_cases: int) -> str:
    lines = ["# Memory-extraction eval", ""]
    lines.append("Offline run against the rule-based mock: this checks the harness, not a model."
                 if mock else f"Live run on Nebius Token Factory, {n_cases} labelled messages.")
    lines += ["", "| Model | Precision | Recall | F1 | TP | FP | FN | Negatives with no memory | Median ms | Errors |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['label']} | {r['precision']:.0%} | {r['recall']:.0%} | {r['f1']:.2f} | {r['tp']} | "
                     f"{r['fp']} | {r['fn']} | {r['negatives_clean']} | {r['median_ms']} | {r['errors']} |")
    for r in rows:
        lines += ["", f"## {r['label']}: recall by type", "",
                  "| Type | Gold | Found | Predicted |", "|---|---|---|---|"]
        for kind, c in sorted(r["by_type"].items()):
            lines.append(f"| {kind} | {c['gold']} | {c['tp']} | {c['pred']} |")
        if r["failures"]:
            lines += ["", f"### {r['label']}: misses and extras", ""]
            lines += [f"- \"{text}\": " + "; ".join(notes) for text, notes in r["failures"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mock", action="store_true", help="offline mock models (checks the harness only)")
    ap.add_argument("--models", default="nano,lightning,super", help="comma-separated model aliases")
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "memory_results.md")
    args = ap.parse_args()
    settings = get_settings()
    mock = args.mock or settings.mock
    if settings.mock and not args.mock:
        print(missing_key_help(ROOT) + "\nRunning against the offline mock instead.", file=sys.stderr)
    client: LLMClient = MockClient(mock_brain) if mock else TokenFactoryClient(settings)
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    rows = []
    for alias in [a.strip() for a in args.models.split(",") if a.strip()]:
        rows.append(run_model(settings, client, alias, cases))
        r = rows[-1]
        print(f"{r['label']:<24} P {r['precision']:.0%}  R {r['recall']:.0%}  F1 {r['f1']:.2f}  "
              f"negatives clean {r['negatives_clean']}  {r['median_ms']} ms")
    args.out.write_text(report(rows, mock, len(cases)), encoding="utf-8")
    print(f"Wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
