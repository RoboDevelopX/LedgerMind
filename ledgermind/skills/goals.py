"""Savings goals: set them, and track progress against the user's real monthly surplus."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from ..db import now_iso
from ..memory.store import MemoryStore
from ..money import fmt, money
from ..periods import add_months, today
from . import SkillContext, skill
from .summary import monthly_baseline


def months_until(deadline: str | None) -> int | None:
    if not deadline:
        return None
    d, t = date.fromisoformat(deadline), today()
    return max((d.year - t.year) * 12 + d.month - t.month, 1)


@skill("set_goal", "Create or update a savings goal (target amount, optional deadline and amount saved so far).",
       {"name": {"type": "string", "description": "short name, e.g. 'Japan trip'"},
        "target_amount": {"type": "number"},
        "deadline": {"type": "string", "description": "YYYY-MM-DD (optional)"},
        "saved_so_far": {"type": "number", "description": "optional, default 0 or unchanged"}},
       ("name", "target_amount"), mutates=True)
def set_goal(ctx: SkillContext, name: str, target_amount: float, deadline: str | None = None,
             saved_so_far: float | None = None) -> dict[str, Any]:
    if target_amount <= 0:
        return {"error": "the target must be positive"}
    if deadline:
        try:
            date.fromisoformat(deadline)
        except ValueError:
            return {"error": f"deadline must be YYYY-MM-DD, got {deadline!r}"}
    name = name.strip()
    ctx.db.execute(
        "INSERT INTO goals(name, target, saved, deadline, created_at) VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE "
        "SET target=excluded.target, saved=COALESCE(?, goals.saved), "
        "deadline=COALESCE(excluded.deadline, goals.deadline)",
        [name, target_amount, saved_so_far or 0, deadline, now_iso(), saved_so_far])
    cur = ctx.settings.currency
    text = f"Saving for {name}: target {fmt(target_amount, cur)}" + (f" by {deadline}" if deadline else "") + "."
    MemoryStore(ctx.db).upsert("goal", text, key=name.lower(), confidence=1.0, importance=0.85, source="user")
    return goal_progress(ctx, name)


@skill("goal_progress",
       "Progress on savings goals: saved, remaining, percent done, months left, the monthly saving each needs, the "
       "user's average monthly surplus, whether the goals fit, and when each would finish at the current surplus.",
       {"name": {"type": "string", "description": "optional: one goal"}}, narrate_with="deep")
def goal_progress(ctx: SkillContext, name: str | None = None) -> dict[str, Any]:
    goals = ctx.db.all("SELECT * FROM goals ORDER BY deadline IS NULL, deadline")
    if name:
        goals = [g for g in goals if g["name"].lower() == name.lower()] or \
                [g for g in goals if name.lower() in g["name"].lower()]
    base = monthly_baseline(ctx)
    surplus = float(base["avg_surplus"])
    rows, needed_total = [], 0.0
    for g in goals:
        remaining = max(g["target"] - g["saved"], 0.0)
        months_left = months_until(g["deadline"])
        needed = remaining / months_left if months_left else None
        needed_total += needed or 0.0
        at_surplus = math.ceil(remaining / surplus) if surplus > 0 and remaining else (0 if not remaining else None)
        rows.append({
            "name": g["name"], "target": money(g["target"]), "saved": money(g["saved"]),
            "remaining": money(remaining), "pct_done": round(g["saved"] / g["target"] * 100, 1),
            "deadline": g["deadline"], "months_left": months_left,
            "needed_per_month": money(needed) if needed is not None else None,
            "needed_pct_of_surplus": round(needed / surplus * 100, 1) if needed and surplus > 0 else None,
            "months_to_finish_using_full_surplus": at_surplus,
            "finish_date_using_full_surplus": add_months(today(), at_surplus).isoformat() if at_surplus else None,
        })
    if not rows:
        return {"goals": [], "message": "No savings goals yet" + (f" matching {name!r}" if name else "") + ".",
                "avg_monthly_surplus": base["avg_surplus"]}
    return {"goals": rows, "avg_monthly_surplus": base["avg_surplus"], "avg_monthly_income": base["avg_income"],
            "total_needed_per_month": money(needed_total), "surplus_after_goals": money(surplus - needed_total),
            "fits_in_surplus": needed_total <= surplus, "baseline_months": base["months"]}
