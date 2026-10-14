"""Recurring-charge detection: subscriptions and bills, cadence, price hikes and possibly forgotten ones."""
from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from ..ledger import frame
from ..money import money
from ..periods import today
from . import SkillContext, skill

# (name, typical gap in days, accepted gap range)
CADENCES = [("weekly", 7, (5, 9)), ("monthly", 30.44, (25, 36)), ("quarterly", 91, (84, 98)),
            ("yearly", 365, (350, 380))]
BILL_CATEGORIES = {"Housing", "Utilities", "Insurance", "Education"}
SKIP_CATEGORIES = {"Groceries", "Dining", "Transport", "Shopping", "Transfers", "Cash", "Income"}
# Services where paying for two at once is rarely intended: a hint one is forgotten.
SERVICE_GROUPS = {
    "music streaming": {"Spotify", "Apple Music", "YouTube Music", "Gaana", "JioSaavn", "Amazon Music"},
    "cloud storage": {"iCloud+", "Google One", "Dropbox"},
}
PRICE_HIKE_MIN_PCT = 3.0


def _cadence(gaps: list[int]) -> tuple[str, float] | None:
    med = statistics.median(gaps)
    for name, typical, (lo, hi) in CADENCES:
        if lo <= med <= hi and sum(lo <= g <= hi for g in gaps) / len(gaps) >= 0.7:
            return name, typical
    return None


def _price_change(amounts: list[float]) -> dict[str, Any] | None:
    """Detect a step change between the earlier stable price and the latest one."""
    if len(amounts) < 3:
        return None
    latest = amounts[-1]
    i = len(amounts) - 1
    while i > 0 and abs(amounts[i - 1] - latest) < 0.01:
        i -= 1
    before = amounts[:i]
    if len(before) < 2 or statistics.pstdev(before) / statistics.mean(before) > 0.03:
        return None  # no stable earlier price to compare with
    old = statistics.median(before)
    pct = (latest - old) / old * 100 if old else 0.0
    if abs(pct) < PRICE_HIKE_MIN_PCT:
        return None
    return {"from": money(old), "to": money(latest), "change_pct": round(pct, 1),
            "direction": "increase" if pct > 0 else "decrease", "charges_at_new_price": len(amounts) - i}


def find_recurring(ctx: SkillContext) -> list[dict[str, Any]]:
    """All recurring outgoing charges in the ledger, most expensive (per month) first."""
    df = frame(ctx.db)
    df = df[(df.amount < 0) & ~df.category.isin(SKIP_CATEGORIES)]
    t = today()
    found = []
    for merchant, g in df.groupby("merchant"):
        g = g.sort_values("date")
        if len(g) < 2:
            continue
        dates = [d.date() for d in g.date]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        cad = _cadence(gaps) if len(g) >= 3 or min(gaps) >= 350 else None
        if cad is None:
            continue
        amounts = [-a for a in g.amount]
        spread = statistics.pstdev(amounts) / statistics.mean(amounts)
        category = g.category.iloc[-1]
        change = _price_change(amounts)
        if spread > (0.35 if category == "Utilities" else 0.25) and change is None:
            continue
        name, days = cad
        last = dates[-1]
        active = (t - last).days <= days * 1.5
        found.append({
            "merchant": merchant, "category": category,
            "kind": "bill" if category in BILL_CATEGORIES else ("savings" if category == "Savings"
                                                                else "subscription"),
            "cadence": name, "amount": money(amounts[-1]),
            "monthly_equivalent": money(statistics.mean(amounts[-3:]) * 30.44 / days),
            "yearly_equivalent": money(statistics.mean(amounts[-3:]) * 365 / days),
            "charges": len(amounts), "first_charged": dates[0].isoformat(), "last_charged": last.isoformat(),
            "next_expected": (last + timedelta(days=round(days))).isoformat() if active else None,
            "active": active, "price_change": change, "flags": [],
        })
    _flag(found)
    found.sort(key=lambda r: -r["monthly_equivalent"])
    return found


def _flag(found: list[dict[str, Any]]) -> None:
    """Price hikes, and older overlapping services in the same group (newest is probably the one in use)."""
    for r in found:
        if r["price_change"] and r["price_change"]["direction"] == "increase":
            r["flags"].append("price_hike")
    for group, names in SERVICE_GROUPS.items():
        members = [r for r in found if r["merchant"] in names and r["active"] and r["kind"] == "subscription"]
        if len(members) < 2:
            continue
        newest = max(members, key=lambda r: r["first_charged"])
        for r in members:
            if r is not newest:
                r["flags"].append("possibly_forgotten")
                r["overlaps_with"] = {"merchant": newest["merchant"], "group": group,
                                      "since": newest["first_charged"]}


@skill("detect_subscriptions",
       "Find subscriptions and recurring bills with their cadence, monthly and yearly cost, next expected charge, "
       "price hikes, and subscriptions that look forgotten (overlapping services).")
def detect_subscriptions(ctx: SkillContext) -> dict[str, Any]:
    """Recurring charges from transaction history. Totals cover active subscriptions only."""
    rows = find_recurring(ctx)
    subs = [r for r in rows if r["kind"] == "subscription" and r["active"]]
    return {
        "subscriptions": subs,
        "bills": [r for r in rows if r["kind"] == "bill" and r["active"]],
        "inactive": [{"merchant": r["merchant"], "last_charged": r["last_charged"]} for r in rows if not r["active"]],
        "subscriptions_monthly_total": money(sum(r["monthly_equivalent"] for r in subs)),
        "subscriptions_yearly_total": money(sum(r["yearly_equivalent"] for r in subs)),
        "price_hikes": [r["merchant"] for r in subs if "price_hike" in r["flags"]],
        "possibly_forgotten": [r["merchant"] for r in subs if "possibly_forgotten" in r["flags"]],
        "as_of": date.isoformat(today()),
    }
