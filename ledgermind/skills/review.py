"""Monthly review: figures computed locally, a structured report (wins, risks, 3 actions) written by Nemotron Ultra,
checked by the number guard, and exportable as Markdown."""
from __future__ import annotations

import json
from typing import Any

from ..agent.numguard import check
from ..db import dumps, now_iso
from ..llm import AllModelsFailed, ChatRequest
from ..llm.jsonutil import parse_json
from ..money import fmt, render_for_model
from ..periods import add_months, resolve_period, today
from ..privacy.minimise import minimise
from . import SkillContext, skill
from .anomalies import find_anomalies
from .subscriptions import find_recurring
from .summary import summarize

REVIEW_PROMPT = """You are Ledgermind's monthly reviewer. Write a short, specific review of the month in DATA.
Rules: use ONLY numbers that appear in DATA and copy them exactly as written (they are already formatted);
never add, subtract or compute anything; informational insights only, not financial or investment advice.
Reply with JSON only:
{"headline": "one sentence that states the month's income, spent and net_cashflow",
 "wins": ["2-3 items"], "risks": ["2-3 items"],
 "actions": ["exactly 3 concrete actions for next month"]}"""


def review_data(ctx: SkillContext, month: str) -> dict[str, Any]:
    """All the figures a review needs, computed from the ledger."""
    period = resolve_period(month)
    s = summarize(ctx, period)
    if s.get("empty"):
        return s
    cats = [c for c in s["by_category"] if c["change_pct"] is not None]
    up = sorted((c for c in cats if c["spent"] > c["previous"]), key=lambda c: c["previous"] - c["spent"])[:3]
    down = sorted((c for c in cats if c["spent"] < c["previous"]), key=lambda c: c["spent"] - c["previous"])[:3]
    income = float(s["income"])
    budgets = []
    for b in ctx.db.all("SELECT * FROM budgets"):
        spent = next((c["spent"] for c in s["by_category"] if c["category"] == b["category"]), 0.0)
        budgets.append({"category": b["category"], "limit": b["monthly_limit"], "spent": spent,
                        "status": "over" if spent > b["monthly_limit"] else "within"})
    subs = find_recurring(ctx)
    month_key = period.start.strftime("%Y-%m")
    return {
        "month": period.label, "income": s["income"], "spent": s["spent"], "saved_or_invested": s["saved_or_invested"],
        "net_cashflow": s["net_cashflow"],
        "savings_rate_pct": round((income - float(s["spent"])) / income * 100, 1) if income else None,
        "spent_change_vs_previous_month_pct": s["compared_with"]["spent_change_pct"],
        "top_categories": s["by_category"][:5], "biggest_increases": up, "biggest_decreases": down,
        "budgets": budgets,
        "subscription_price_hikes": [{"merchant": r["merchant"], **r["price_change"]} for r in subs
                                     if "price_hike" in r["flags"]],
        "possibly_forgotten_subscriptions": [{"merchant": r["merchant"], "monthly": r["monthly_equivalent"]}
                                             for r in subs if "possibly_forgotten" in r["flags"]],
        "anomalies": [{k: a[k] for k in ("kind", "reason", "amount") if k in a} | {"merchant": a.get("merchant")}
                      for a in find_anomalies(ctx, month_key)][:5],
    }


def _fallback_report(data: dict[str, Any], currency: str) -> dict[str, Any]:
    """Deterministic report used when no model is available or its draft fails the number guard twice."""
    v = render_for_model(data, currency)
    wins = [f"You earned {v['income']} and spent {v['spent']}, leaving {v['net_cashflow']} after "
            f"{v['saved_or_invested']} went to savings."]
    wins += [f"{c['category']} came down to {c['spent']} from {c['previous']}." for c in v["biggest_decreases"][:2]]
    risks = [f"{c['category']} rose to {c['spent']} from {c['previous']}." for c in v["biggest_increases"][:2]]
    risks += [f"{a['reason']} ({a['amount']})" for a in v["anomalies"][:1]]
    actions = [f"Set a {c['category']} budget near its previous level of {c['previous']}."
               for c in v["biggest_increases"][:1]]
    actions += [f"Decide whether you still need {s['merchant']} ({s['monthly']} a month)."
                for s in v["possibly_forgotten_subscriptions"][:1]]
    actions += [f"Review the {p['merchant']} price change from {p['from']} to {p['to']}."
                for p in v["subscription_price_hikes"][:1]]
    actions += ["Ask me for a what-if on your largest category to see what a cut would free up."]
    return {"headline": f"{v['month']}: spent {v['spent']} against income of {v['income']}.",
            "wins": wins[:3], "risks": risks[:3] or ["No major risks stood out."], "actions": actions[:3]}


def _model_report(ctx: SkillContext, data: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    view, _ = minimise(data, strict=ctx.settings.privacy.strict_mode,
                       max_rows=ctx.settings.privacy.max_rows_per_tool, currency=ctx.settings.currency)
    messages = [{"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": "DATA:\n" + json.dumps(view, ensure_ascii=False, indent=1)}]
    for _ in range(2):
        try:
            result, rec = ctx.router.chat("deep", ChatRequest(messages=messages, task="monthly_review",
                                                              json_mode=True, max_tokens=1500, temperature=0.4))
            report = parse_json(result.content)
        except (AllModelsFailed, ValueError):
            return None, ""
        if not isinstance(report, dict) or not all(isinstance(report.get(k), list) for k in ("wins", "risks",
                                                                                             "actions")):
            continue
        guard = check(json.dumps(report, ensure_ascii=False), view)
        if guard.ok:
            report["actions"] = report["actions"][:3]
            return report, rec.label
        messages.append({"role": "user", "content": f"These numbers are not in DATA: {guard.unsupported}. "
                                                    "Rewrite using only numbers copied from DATA."})
    return None, ""


@skill("monthly_review",
       "Write a monthly review for a month (default: last full month): income, spending, savings rate, biggest "
       "changes, budgets, subscription issues and anomalies, with wins, risks and exactly 3 actions. "
       "Exportable as Markdown.",
       {"month": {"type": "string", "description": "YYYY-MM (default last month)"}})
def monthly_review(ctx: SkillContext, month: str | None = None) -> dict[str, Any]:
    month = month or add_months(today(), -1).strftime("%Y-%m")
    data = review_data(ctx, month)
    if data.get("empty"):
        return data
    report, model = _model_report(ctx, data) if ctx.router is not None else (None, "")
    if report is None:
        report, model = _fallback_report(data, ctx.settings.currency), "deterministic template"
    ctx.db.execute("INSERT OR REPLACE INTO reviews(month, data, report, model, created_at) VALUES (?,?,?,?,?)",
                   [month, dumps(data), dumps(report), model, now_iso()])
    return {"month": data["month"], "figures": data, "report": report, "written_by": model,
            "export_markdown": f"/api/review/{month}.md"}


def review_markdown(ctx: SkillContext, month: str) -> str | None:
    """A saved review as Markdown (tables from the figures, narrative from the report)."""
    row = ctx.db.one("SELECT * FROM reviews WHERE month=?", [month])
    if row is None:
        return None
    data, report = json.loads(row["data"]), json.loads(row["report"])
    cur = ctx.settings.currency

    def f(x: float | None) -> str:
        return fmt(x, cur) if x is not None else "-"

    lines = [f"# Ledgermind monthly review: {data['month']}", "", f"_{report.get('headline', '')}_", "",
             "| | Amount |", "|---|---:|", f"| Income | {f(data['income'])} |", f"| Spent | {f(data['spent'])} |",
             f"| Saved or invested | {f(data['saved_or_invested'])} |", f"| Net cashflow | {f(data['net_cashflow'])} |",
             f"| Savings rate | {data['savings_rate_pct']}% |", "", "## Top categories", "",
             "| Category | Spent | Previous month | Change |", "|---|---:|---:|---:|"]
    lines += [f"| {c['category']} | {f(c['spent'])} | {f(c['previous'])} | "
              f"{'' if c['change_pct'] is None else str(c['change_pct']) + '%'} |" for c in data["top_categories"]]
    for title, key in (("Wins", "wins"), ("Risks", "risks"), ("Actions for next month", "actions")):
        lines += ["", f"## {title}", ""] + [f"{i}. {x}" if key == "actions" else f"- {x}"
                                            for i, x in enumerate(report.get(key, []), 1)]
    lines += ["", "---", f"Written by {row['model']} on {row['created_at'][:10]}. Figures computed locally by "
              "Ledgermind. Informational insights only, not financial advice."]
    return "\n".join(lines) + "\n"
