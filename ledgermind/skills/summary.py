"""Spending summary by category, merchant and period, compared with the previous period."""
from __future__ import annotations

from typing import Any

import pandas as pd

from ..ledger import date_range, frame
from ..merchants import NON_SPENDING, closest_category
from ..money import money
from ..periods import PERIOD_HELP, Period, add_months, month_end, resolve_period, today
from . import SkillContext, skill


def pct_change(new: float, old: float) -> float | None:
    """Percent change, rounded to 1 decimal; None when there is no baseline."""
    return round((new - old) / old * 100, 1) if old else None


def totals(df: pd.DataFrame) -> dict[str, float]:
    """Income, spending and savings for a frame of transactions."""
    spend = df[(df.amount < 0) & ~df.category.isin(NON_SPENDING)]
    refunds = df[(df.amount > 0) & ~df.category.isin(NON_SPENDING)]
    return {
        "income": float(df[(df.amount > 0) & (df.category == "Income")].amount.sum()),
        "spent": float(-spend.amount.sum() - refunds.amount.sum()),
        "saved": float(-df[(df.amount < 0) & (df.category == "Savings")].amount.sum()),
    }


def spending_by(df: pd.DataFrame, column: str) -> pd.Series:
    """Net spending per group (refunds reduce it), largest first."""
    rows = df[~df.category.isin(NON_SPENDING)]
    return (-rows.groupby(column).amount.sum()).sort_values(ascending=False)


def summarize(ctx: SkillContext, period: Period, category: str | None = None, merchant: str | None = None,
              top_n: int = 6) -> dict[str, Any]:
    """The summary for one period plus a comparison with the previous one. Used by other skills too."""
    prev = period.previous()
    cur_df, prev_df = frame(ctx.db, period.start, period.end), frame(ctx.db, prev.start, prev.end)
    if category:
        cur_df, prev_df = cur_df[cur_df.category == category], prev_df[prev_df.category == category]
    if merchant:
        pick = lambda d: d[d.merchant.str.lower().str.contains(merchant.lower(), regex=False)]  # noqa: E731
        cur_df, prev_df = pick(cur_df), pick(prev_df)
    if cur_df.empty:
        what = " ".join(x for x in (category, merchant) if x) or "any"
        lo, hi = date_range(ctx.db)
        hint = (f" The ledger covers {lo} to {hi}; if the user meant a period inside that, call the tool again with "
                "it (a bare month name such as september resolves to its latest occurrence).") if lo else ""
        return {"period": period.to_dict(), "empty": True,
                "message": f"No {what} transactions between {period.start} and {period.end}.{hint}"}
    cur, old = totals(cur_df), totals(prev_df)
    by_cat, prev_cat = spending_by(cur_df, "category"), spending_by(prev_df, "category")
    spent_total = cur["spent"] or 1.0
    result: dict[str, Any] = {
        "period": period.to_dict(),
        "transactions": int(len(cur_df)),
        "income": money(cur["income"]),
        "spent": money(cur["spent"]),
        "saved_or_invested": money(cur["saved"]),
        "net_cashflow": money(cur["income"] - cur["spent"] - cur["saved"]),
        "by_category": [{"category": c, "spent": money(v), "share_pct": round(v / spent_total * 100, 1),
                         "previous": money(prev_cat.get(c, 0.0)), "change_pct": pct_change(v, prev_cat.get(c, 0.0))}
                        for c, v in by_cat.items() if v > 0][:12],
        "top_merchants": [{"merchant": m, "spent": money(v), "count": int((cur_df.merchant == m).sum())}
                          for m, v in spending_by(cur_df, "merchant").head(top_n).items() if v > 0],
        "compared_with": {"period": prev.to_dict(), "income": money(old["income"]), "spent": money(old["spent"]),
                          "spent_change": money(cur["spent"] - old["spent"]),
                          "spent_change_pct": pct_change(cur["spent"], old["spent"])},
    }
    if category or merchant:
        result["filter"] = {"category": category, "merchant": merchant}
        result["monthly"] = [{"month": m, "spent": money(v)}
                             for m, v in (-cur_df[cur_df.amount < 0].groupby("month").amount.sum()).items()]
    return result


@skill("spending_summary",
       "Income, spending, savings and net cashflow for a period, broken down by category and top merchants, "
       "with the change versus the previous period. Optionally filter to one category or merchant.",
       {"period": {"type": "string", "description": PERIOD_HELP},
        "category": {"type": "string", "description": "optional category filter, e.g. Dining"},
        "merchant": {"type": "string", "description": "optional merchant filter, e.g. Swiggy"}})
def spending_summary(ctx: SkillContext, period: str = "this_month", category: str | None = None,
                     merchant: str | None = None) -> dict[str, Any]:
    """Deterministic summary from the ledger. Every figure, percentage and change is computed here."""
    cat = closest_category(category) if category else None
    if category and cat is None:
        return {"error": f"unknown category {category!r}"}
    return summarize(ctx, resolve_period(period), cat, merchant)


def monthly_baseline(ctx: SkillContext, months: int = 3) -> dict[str, Any]:
    """Average month over the last `months` full months: income, spending (total and by category), savings and
    surplus (income minus spending). The basis for budgets, goals and what-if scenarios."""
    start = add_months(today(), -months)
    df = frame(ctx.db, start, month_end(add_months(today(), -1)))
    n = max(df.month.nunique(), 1) if not df.empty else 1
    t = totals(df) if not df.empty else {"income": 0.0, "spent": 0.0, "saved": 0.0}
    by_cat = spending_by(df, "category") / n if not df.empty else pd.Series(dtype=float)
    return {"months": int(df.month.nunique()) if not df.empty else 0,
            "from": start.isoformat(), "avg_income": money(t["income"] / n), "avg_spent": money(t["spent"] / n),
            "avg_saved_or_invested": money(t["saved"] / n), "avg_surplus": money((t["income"] - t["spent"]) / n),
            "by_category": {c: money(v) for c, v in by_cat.items() if v > 0}}
