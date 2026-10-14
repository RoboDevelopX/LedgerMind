"""Categorisation eval: accuracy on labelled bank descriptions, with an ablation of the pipeline.

    python eval/categorisation_eval.py                 # live (needs NEBIUS_API_KEY): Nano, Lightning, Super
    python eval/categorisation_eval.py --models nano
    python eval/categorisation_eval.py --mock          # offline: checks the harness, not a model

Three pipelines are scored on eval/categorisation_cases.yaml:
  rules only      local rules; anything they don't know becomes "Other" (what happens with no model at all)
  model only      every normalised merchant goes to the model (no rules)
  rules + model   production: rules first, the model only for merchants the rules don't know
plus the share of the model's batch that rules saved (fewer tokens, less data sent). Results go to
eval/categorisation_results.md.
"""
from __future__ import annotations

import argparse
import sys
import time
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
from ledgermind.merchants import clean_description, closest_category, rule_match  # noqa: E402
from ledgermind.skills import SkillContext  # noqa: E402
from ledgermind.skills.categorize import BATCH, _ask_model  # noqa: E402

CASES = ROOT / "eval" / "categorisation_cases.yaml"


def ask(ctx: SkillContext, cases: list[dict[str, Any]]) -> tuple[dict[str, str], float]:
    """Model categories for these cases' merchants (by merchant name) and the wall-clock ms spent."""
    todo: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        todo.setdefault(clean_description(c["d"]), []).append({"direction": "in" if c["amount"] > 0 else "out"})
    merchants, out = list(todo), {}
    start = time.monotonic()
    for i in range(0, len(merchants), BATCH):
        for m, label in _ask_model(ctx, merchants[i:i + BATCH], todo).items():
            out[m] = closest_category(str(label.get("category", ""))) or "Other"
    return out, (time.monotonic() - start) * 1000


def accuracy(pred: list[str], cases: list[dict[str, Any]]) -> float:
    return sum(p == c["label"] for p, c in zip(pred, cases, strict=True)) / len(cases)


def run_model(settings: Settings, client: LLMClient, alias: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    s = settings.model_copy(deep=True)
    s.roles["fast"] = [alias]
    ctx = SkillContext(db=Database(":memory:"), settings=s, router=ModelRouter(s, client))
    ruled = {c["d"]: rule_match(c["d"]) for c in cases}
    unknown = [c for c in cases if ruled[c["d"]] is None]

    all_labels, ms_all = ask(ctx, cases)
    model_only = [all_labels.get(clean_description(c["d"]), "Other") for c in cases]
    rest_labels, ms_rest = ask(ctx, unknown)
    combined = [ruled[c["d"]][1] if ruled[c["d"]] else rest_labels.get(clean_description(c["d"]), "Other")
                for c in cases]
    misses = [f"{c['d']}: got {p}, want {c['label']}" for p, c in zip(combined, cases, strict=True) if p != c["label"]]
    return {"label": s.models[alias].label, "model_only": accuracy(model_only, cases),
            "combined": accuracy(combined, cases),
            "on_unknown": accuracy([rest_labels.get(clean_description(c["d"]), "Other") for c in unknown], unknown)
            if unknown else 1.0,
            "ms_all": int(ms_all), "ms_rest": int(ms_rest), "misses": misses}


def report(cases, rows, rules_cov, rules_acc, rules_only, mock) -> str:
    lines = ["# Categorisation eval", "",
             "Offline run against the rule-based mock: this checks the harness, not a model." if mock else
             f"Live run on Nebius Token Factory, {len(cases)} labelled bank descriptions.", "",
             f"Local rules recognise {rules_cov:.0%} of descriptions, and are right on {rules_acc:.0%} of those. "
             f"Rules alone (unknown merchants become Other) score **{rules_only:.0%}**.", "",
             "| Model | Model only | Rules + model (production) | Model on merchants rules don't know | "
             "ms, all merchants | ms, unknown only |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['label']} | {r['model_only']:.0%} | {r['combined']:.0%} | {r['on_unknown']:.0%} | "
                     f"{r['ms_all']} | {r['ms_rest']} |")
    for r in rows:
        if r["misses"]:
            lines += ["", f"## {r['label']}: errors (rules + model)", ""] + [f"- {m}" for m in r["misses"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mock", action="store_true", help="offline mock models (checks the harness only)")
    ap.add_argument("--models", default="nano,lightning,super", help="comma-separated model aliases")
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "categorisation_results.md")
    args = ap.parse_args()
    settings = get_settings()
    mock = args.mock or settings.mock
    if settings.mock and not args.mock:
        print(missing_key_help(ROOT) + "\nRunning against the offline mock instead.", file=sys.stderr)
    client: LLMClient = MockClient(mock_brain) if mock else TokenFactoryClient(settings)
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    ruled = [rule_match(c["d"]) for c in cases]
    known = [(r, c) for r, c in zip(ruled, cases, strict=True) if r]
    rules_cov = len(known) / len(cases)
    rules_acc = sum(r[1] == c["label"] for r, c in known) / len(known) if known else 0.0
    rules_only = accuracy([r[1] if r else "Other" for r in ruled], cases)
    print(f"rules: coverage {rules_cov:.0%}, precision {rules_acc:.0%}, rules-only accuracy {rules_only:.0%}")
    rows = []
    for alias in [a.strip() for a in args.models.split(",") if a.strip()]:
        rows.append(run_model(settings, client, alias, cases))
        r = rows[-1]
        print(f"{r['label']:<24} model only {r['model_only']:.0%}  rules+model {r['combined']:.0%}  "
              f"unknown {r['on_unknown']:.0%}")
    args.out.write_text(report(cases, rows, rules_cov, rules_acc, rules_only, mock), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
