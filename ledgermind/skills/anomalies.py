"""Anomaly detection: statistics first, a model only explains.

Kinds
-----
duplicate        same merchant and amount within 2 days
unusual_amount   far above this merchant's usual charge (robust z-score on history)
large_one_off    a big charge from a merchant with little or no history
category_spike   a category's month well above its trailing average
"""
from __future__ import annotations

import statistics
from typing import Any

import pandas as pd

from ..ledger import frame
from ..merchants import NON_SPENDING
from ..money import money
from ..periods import PERIOD_HELP, add_months, month_key, resolve_period
from . import SkillContext, skill

# Minimum amounts worth flagging, per currency.
FLOORS = {"INR": {"duplicate": 100, "one_off": 10000, "spike": 3000},
          "USD": {"duplicate": 5, "one_off": 300, "spike": 75}}
EXCLUDE = NON_SPENDING | {"Housing"}


def _floors(currency: str) -> dict[str, float]:
    return FLOORS.get(currency, FLOORS["INR"])


def _duplicates(df: pd.DataFrame, floor: float) -> list[dict[str, Any]]:
    out = []
    debits = df[(df.amount <= -floor) & ~df.category.isin(EXCLUDE)].sort_values("date")
    for (merchant, amount), g in debits.groupby(["merchant", "amount"]):
        rows = g.to_dict("records")
        for a, b in zip(rows, rows[1:], strict=False):
            gap = (b["date"] - a["date"]).days
            if gap <= 2:
                out.append({"kind": "duplicate", "date": b["date"].date().isoformat(), "merchant": merchant,
                            "amount": money(-amount), "transaction_ids": [int(a["id"]), int(b["id"])],
                            "score": 5.0,
                            "reason": f"Charged twice by {merchant} for the same amount "
                                      f"{'on the same day' if gap == 0 else f'{gap} day(s) apart'}."})
    return out


def _merchant_outliers(df: pd.DataFrame, history: pd.DataFrame, floor: float,
                       all_debits: pd.Series) -> list[dict[str, Any]]:
    out = []
    big = all_debits.quantile(0.99) if len(all_debits) >= 20 else float("inf")
    for row in df[(df.amount < 0) & ~df.category.isin(EXCLUDE)].to_dict("records"):
        amount = -row["amount"]
        past = -history[(history.merchant == row["merchant"]) & (history.date < row["date"])
                        & (history.amount < 0)].amount
        if len(past) >= 4:
            med = statistics.median(past)
            mad = statistics.median([abs(x - med) for x in past]) or med * 0.1
            z = (amount - med) / (1.4826 * mad)
            if z > 4 and amount > 2 * med and amount - med >= floor / 10:
                out.append({"kind": "unusual_amount", "date": row["date"].date().isoformat(),
                            "merchant": row["merchant"], "category": row["category"], "amount": money(amount),
                            "typical": money(med), "times_typical": round(amount / med, 1),
                            "transaction_ids": [int(row["id"])], "score": min(z, 10.0),
                            "reason": f"{round(amount / med, 1)}x the usual {row['merchant']} charge."})
        elif len(past) < 2 and amount >= max(floor, big):
            out.append({"kind": "large_one_off", "date": row["date"].date().isoformat(),
                        "merchant": row["merchant"], "category": row["category"], "amount": money(amount),
                        "transaction_ids": [int(row["id"])], "score": 4.0 + amount / big if big else 4.0,
                        "reason": "One of your largest charges this year, from a merchant you rarely use."})
    return out


def _category_spikes(history: pd.DataFrame, months: list[str], floor: float) -> list[dict[str, Any]]:
    spend = history[(history.amount < 0) & ~history.category.isin(EXCLUDE)]
    table = (-spend.groupby(["month", "category"]).amount.sum()).unstack(fill_value=0.0)
    out = []
    for month in months:
        if month not in table.index:
            continue
        prior = table[table.index < month].tail(6)
        if len(prior) < 3:
            continue
        for category, value in table.loc[month].items():
            avg = float(prior[category].mean())
            sd = float(prior[category].std(ddof=0))
            if avg > 0 and value >= 1.5 * avg and value - avg >= floor and (sd == 0 or (value - avg) / sd >= 2):
                out.append({"kind": "category_spike", "month": month, "category": category,
                            "amount": money(value), "typical": money(avg),
                            "above_typical": money(value - avg), "change_pct": round((value - avg) / avg * 100, 1),
                            "score": 3.0 + min((value - avg) / avg, 5.0),
                            "reason": f"{category} spending in {month} was well above its 6-month average."})
    return out


def find_anomalies(ctx: SkillContext, period: str = "last_90_days") -> list[dict[str, Any]]:
    """Anomalies whose date falls in `period`, highest score first."""
    p = resolve_period(period)
    floors = _floors(ctx.settings.currency)
    history = frame(ctx.db, add_months(p.start, -12), p.end)
    window = history[(history.date >= pd.Timestamp(p.start)) & (history.date <= pd.Timestamp(p.end))]
    debits = -history[(history.amount < 0) & ~history.category.isin(EXCLUDE)].amount
    months, m = [], p.start.replace(day=1)
    while m <= p.end:
        months.append(month_key(m))
        m = add_months(m, 1)
    singles = _duplicates(window, floors["duplicate"]) + _merchant_outliers(window, history, floors["one_off"], debits)
    # A spike that one flagged charge already explains is the same story twice.
    spikes = [s for s in _category_spikes(history, months, floors["spike"])
              if not any(a.get("category") == s["category"] and a["date"].startswith(s["month"])
                         and a["amount"] >= 0.7 * s["above_typical"] for a in singles)]
    found = singles + spikes
    found.sort(key=lambda a: -a["score"])
    for a in found:
        a["score"] = round(a["score"], 1)
    return found


@skill("detect_anomalies",
       "Find unusual charges, possible duplicate charges, big one-off purchases and category spending spikes in a "
       "period (default last 90 days). Statistics are computed locally; explain them in plain language.",
       {"period": {"type": "string", "description": PERIOD_HELP}}, narrate_with="deep")
def detect_anomalies(ctx: SkillContext, period: str = "last_90_days") -> dict[str, Any]:
    """Anomalies with the numbers behind each one. Nemotron Ultra writes the explanation."""
    p = resolve_period(period)
    found = find_anomalies(ctx, period)
    return {"period": p.to_dict(), "count": len(found), "anomalies": found[:10],
            "by_kind": {k: sum(a["kind"] == k for a in found) for k in
                        ("duplicate", "unusual_amount", "large_one_off", "category_spike")}}
