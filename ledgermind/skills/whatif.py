"""What-if scenarios, computed deterministically from the user's own baseline; Nemotron Ultra narrates."""
from __future__ import annotations

import math
from typing import Any

from ..merchants import closest_category
from ..money import money
from . import SkillContext, skill
from .summary import monthly_baseline


@skill("what_if_simulator",
       "Simulate changing spending in one or more categories (e.g. cut Dining by 30%) or income, over a number of "
       "months. Returns monthly and total savings and how much sooner each savings goal would be reached.",
       {"changes": {"type": "array", "description": "list of {category, change_pct}; negative = spend less",
                    "items": {"type": "object", "properties": {"category": {"type": "string"},
                                                               "change_pct": {"type": "number"}},
                              "required": ["category", "change_pct"]}},
        "category": {"type": "string", "description": "shortcut for a single change"},
        "change_pct": {"type": "number", "description": "shortcut for a single change, e.g. -30"},
        "income_change_pct": {"type": "number", "description": "optional income change, e.g. 10"},
        "months": {"type": "integer", "description": "horizon, default 12"}},
       narrate_with="deep")
def what_if_simulator(ctx: SkillContext, changes: list[dict[str, Any]] | None = None, category: str | None = None,
                      change_pct: float | None = None, income_change_pct: float = 0.0,
                      months: int = 12) -> dict[str, Any]:
    changes = list(changes or [])
    if category is not None and change_pct is not None:
        changes.append({"category": category, "change_pct": change_pct})
    if not changes and not income_change_pct:
        return {"error": "give at least one category change (e.g. Dining -30) or an income change"}
    months = min(max(int(months), 1), 120)
    base = monthly_baseline(ctx)
    if base["months"] == 0:
        return {"error": "not enough history: import at least one full month of transactions"}
    rows, monthly_delta = [], 0.0
    for ch in changes:
        cat = closest_category(str(ch.get("category", "")))
        pct = float(ch.get("change_pct", 0))
        if cat is None:
            return {"error": f"unknown category {ch.get('category')!r}"}
        current = float(base["by_category"].get(cat, 0.0))
        new = current * (1 + pct / 100)
        monthly_delta += current - new
        rows.append({"category": cat, "change_pct": pct, "current_monthly": money(current),
                     "new_monthly": money(new), "monthly_difference": money(current - new),
                     "total_over_horizon": money((current - new) * months)})
    income_delta = float(base["avg_income"]) * income_change_pct / 100
    old_surplus = float(base["avg_surplus"])
    new_surplus = old_surplus + monthly_delta + income_delta
    goals = []
    for g in ctx.db.all("SELECT * FROM goals"):
        remaining = max(g["target"] - g["saved"], 0.0)
        before = math.ceil(remaining / old_surplus) if old_surplus > 0 else None
        after = math.ceil(remaining / new_surplus) if new_surplus > 0 else None
        goals.append({"name": g["name"], "remaining": money(remaining), "months_to_goal_before": before,
                      "months_to_goal_after": after,
                      "months_sooner": (before - after) if before is not None and after is not None else None})
    return {"horizon_months": months, "baseline_months": base["months"], "changes": rows,
            "income_change_pct": income_change_pct, "income_change_monthly": money(income_delta),
            "monthly_saving_change": money(monthly_delta + income_delta),
            "total_change_over_horizon": money((monthly_delta + income_delta) * months),
            "surplus_before": money(old_surplus), "surplus_after": money(new_surplus),
            "goals": goals, "assumption": "averages of the last full months repeat; nothing else changes"}
