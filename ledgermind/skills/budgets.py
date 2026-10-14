"""Monthly category budgets: set one, and check this month's pace against each."""
from __future__ import annotations

from typing import Any

from ..db import now_iso
from ..ledger import frame
from ..memory.store import MemoryStore
from ..merchants import CATEGORIES, NON_SPENDING, closest_category
from ..money import fmt, money
from ..periods import month_end, month_start, today
from . import SkillContext, skill
from .summary import monthly_baseline, spending_by

SPENDING_CATEGORIES = [c for c in CATEGORIES if c not in NON_SPENDING]


@skill("set_budget", "Create or change a monthly spending limit for a category.",
       {"category": {"type": "string", "enum": SPENDING_CATEGORIES},
        "monthly_limit": {"type": "number", "description": "amount per month"}},
       ("category", "monthly_limit"), mutates=True)
def set_budget(ctx: SkillContext, category: str, monthly_limit: float) -> dict[str, Any]:
    """Stores the budget and a memory of it."""
    cat = closest_category(category)
    if cat is None or cat in NON_SPENDING:
        return {"error": f"{category!r} is not a spending category"}
    if monthly_limit <= 0:
        return {"error": "the monthly limit must be positive"}
    ctx.db.execute("INSERT INTO budgets(category, monthly_limit, created_at) VALUES (?,?,?) ON CONFLICT(category) "
                   "DO UPDATE SET monthly_limit=excluded.monthly_limit", [cat, monthly_limit, now_iso()])
    MemoryStore(ctx.db).upsert("preference", f"Monthly {cat} budget is {fmt(monthly_limit, ctx.settings.currency)}.",
                               key=f"budget {cat.lower()}", confidence=1.0, importance=0.7, source="user")
    avg = monthly_baseline(ctx)["by_category"].get(cat)
    return {"category": cat, "monthly_limit": money(monthly_limit),
            "recent_monthly_average": avg if avg is not None else money(0)}


def budget_rows(ctx: SkillContext) -> list[dict[str, Any]]:
    """Month-to-date status for every budget."""
    t = today()
    start, end = month_start(t), month_end(t)
    elapsed, total_days = t.day, end.day
    spent = spending_by(frame(ctx.db, start, t), "category")
    alert = ctx.settings.proactive.budget_alert_pct
    rows = []
    for b in ctx.db.all("SELECT * FROM budgets ORDER BY category"):
        used = max(float(spent.get(b["category"], 0.0)), 0.0)
        limit = b["monthly_limit"]
        projected = used / elapsed * total_days
        pct = used / limit * 100
        status = "over" if used > limit else ("at_risk" if pct >= alert or projected > limit else "on_track")
        rows.append({"category": b["category"], "monthly_limit": money(limit), "spent": money(used),
                     "remaining": money(limit - used), "pct_used": round(pct, 1),
                     "projected_month_end": money(projected), "projected_pct": round(projected / limit * 100, 1),
                     "per_day_left": money(max(limit - used, 0) / max(total_days - elapsed + 1, 1)),
                     "status": status})
    return rows


@skill("budget_check",
       "This month's spending against each budget: amount used, percent used, projected month-end spend at the "
       "current pace, and status (on_track, at_risk, over). Suggests budgets if none are set.",
       {"category": {"type": "string", "description": "optional: one category"}})
def budget_check(ctx: SkillContext, category: str | None = None) -> dict[str, Any]:
    t = today()
    rows = budget_rows(ctx)
    if category:
        cat = closest_category(category)
        rows = [r for r in rows if r["category"] == cat]
    result: dict[str, Any] = {"month": t.strftime("%B %Y"), "day_of_month": t.day,
                              "days_in_month": month_end(t).day, "budgets": rows}
    if not rows:
        base = monthly_baseline(ctx)["by_category"]
        variable = {c: v for c, v in base.items() if c not in ("Housing", "Utilities", "Insurance", "Savings")}
        result["message"] = "No budget set" + (f" for {category}" if category else "") + "."
        result["suggestions"] = [{"category": c, "recent_monthly_average": v}
                                 for c, v in sorted(variable.items(), key=lambda kv: -kv[1])[:3]]
    return result
