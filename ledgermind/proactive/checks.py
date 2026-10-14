"""Proactive checks: things worth telling the user before they ask.

Each check reads the ledger, computes its figures in code and returns notices. A notice has a stable `key`, so a
re-run never repeats a card, and a dismissed card stays dismissed. No model is involved: the wording is templated
from computed figures, which keeps the "no money arithmetic in the LLM" rule trivially true here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..db import dumps, now_iso
from ..ledger import count, frame
from ..logs import get_logger, log_event
from ..memory.store import MemoryStore
from ..money import fmt
from ..periods import month_end, today
from ..skills import SkillContext
from ..skills.anomalies import find_anomalies
from ..skills.budgets import budget_rows
from ..skills.subscriptions import find_recurring
from ..skills.summary import spending_by

log = get_logger(__name__)

NEW_SUBSCRIPTION_DAYS = 75
DIGEST_WEEKS = 12
SEVERITY_RANK = {"alert": 0, "warn": 1, "info": 2}


@dataclass
class Notice:
    kind: str
    key: str
    title: str
    body: str
    severity: str = "info"          # info | warn | alert
    ask: str | None = None          # a follow-up question the UI can send to the agent
    data: dict[str, Any] = field(default_factory=dict)


def new_subscriptions(ctx: SkillContext) -> list[dict[str, Any]]:
    """Subscription merchants first charged in the last `NEW_SUBSCRIPTION_DAYS` days and charged again at the same
    price about a month later. The full recurring detector needs three charges, which would be too late to warn."""
    df = frame(ctx.db)
    if df.empty:
        return []
    subs = df[(df.amount < 0) & (df.category == "Subscriptions")]
    t, found = today(), []
    for merchant, g in subs.groupby("merchant"):
        dates = sorted(d.date() for d in g.date)
        amounts = [-a for a in g.sort_values("date").amount]
        if (t - dates[0]).days > NEW_SUBSCRIPTION_DAYS or len(dates) < 2:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:], strict=False)]
        if all(25 <= gap <= 35 for gap in gaps) and max(amounts) - min(amounts) <= 0.03 * max(amounts):
            found.append({"merchant": merchant, "amount": amounts[-1], "since": dates[0].isoformat(),
                          "charges": len(dates)})
    return found


def subscription_notices(ctx: SkillContext) -> list[Notice]:
    """New subscriptions, price rises and probably forgotten ones."""
    cur = ctx.settings.currency
    out = [Notice("subscription", f"new-sub:{n['merchant']}:{n['since']}", f"New subscription: {n['merchant']}",
                  f"{fmt(n['amount'], cur)} a month since {_nice(n['since'])}, about {fmt(n['amount'] * 12, cur)} "
                  "a year if it continues.", ask=f"Tell me about my {n['merchant']} subscription")
           for n in new_subscriptions(ctx)]
    for r in find_recurring(ctx):
        if r["kind"] != "subscription" or not r["active"]:
            continue
        m = r["merchant"]
        if "price_hike" in r["flags"] and r["price_change"]:
            pc = r["price_change"]
            out.append(Notice("price_hike", f"price:{m}:{pc['to']}", f"{m} raised its price",
                              f"From {fmt(pc['from'], cur)} to {fmt(pc['to'], cur)} ({pc['change_pct']}% more).",
                              severity="warn", ask=f"Is the {m} price rise worth it?"))
        if "possibly_forgotten" in r["flags"]:
            other = r.get("overlaps_with") or {}
            body = (f"You still pay {fmt(r['amount'], cur)} {r['cadence']} for {m}"
                    + (f", but have also paid for {other['merchant']} since {_nice(other['since'])}."
                       if other else "."))
            out.append(Notice("subscription", f"forgotten:{m}", f"Still using {m}?", body, severity="warn",
                              ask=f"Should I cancel {m}?"))
    return out


def budget_notices(ctx: SkillContext) -> list[Notice]:
    """Budgets at or past the alert threshold, or on pace to overshoot."""
    cur, t = ctx.settings.currency, today()
    left = (month_end(t) - t).days
    out = []
    for b in budget_rows(ctx):
        if b["status"] == "on_track":
            continue
        month = t.strftime("%Y-%m")
        if b["status"] == "over":
            out.append(Notice("budget", f"budget-over:{b['category']}:{month}", f"{b['category']} budget exceeded",
                              f"{fmt(b['spent'], cur)} spent against {fmt(b['monthly_limit'], cur)}, with {left} days "
                              "left this month.", severity="alert", ask=f"Where did my {b['category']} money go?"))
        else:
            out.append(Notice("budget", f"budget-risk:{b['category']}:{month}",
                              f"{b['category']} budget {b['pct_used']}% used",
                              f"{fmt(b['spent'], cur)} of {fmt(b['monthly_limit'], cur)} with {left} days left; at "
                              f"this pace you'll reach {fmt(b['projected_month_end'], cur)}.", severity="warn",
                              ask=f"How can I stay within my {b['category']} budget?"))
    return out


def anomaly_notices(ctx: SkillContext) -> list[Notice]:
    """Duplicate charges, unusual amounts and large one-offs from the last 30 days."""
    cur = ctx.settings.currency
    out = []
    for a in find_anomalies(ctx, "last_30_days"):
        if a["kind"] == "category_spike":
            continue
        title = {"duplicate": f"Possible duplicate charge from {a['merchant']}",
                 "unusual_amount": f"Unusual {a['merchant']} charge",
                 "large_one_off": f"Large charge from {a['merchant']}"}[a["kind"]]
        out.append(Notice("anomaly", f"anomaly:{a['kind']}:{a['merchant']}:{a['date']}", title,
                          f"{fmt(a['amount'], cur)} on {_nice(a['date'])}. {a['reason']}",
                          severity="alert" if a["kind"] == "duplicate" else "warn",
                          ask="Anything unusual in the last 30 days?"))
    return out


def weekly_digest(ctx: SkillContext) -> list[Notice]:
    """Last 7 days against a typical week of the previous 12."""
    cur, t = ctx.settings.currency, today()
    week_start = t - timedelta(days=6)
    week = frame(ctx.db, week_start, t)
    history = frame(ctx.db, week_start - timedelta(weeks=DIGEST_WEEKS), week_start - timedelta(days=1))
    if week.empty or history.empty:
        return []
    spent = float(spending_by(week, "category").sum())
    by_cat = spending_by(week, "category")
    typical = float(spending_by(history, "category").sum()) / DIGEST_WEEKS
    top = by_cat.index[0] if not by_cat.empty else None
    diff = spent - typical
    trend = (f"{fmt(abs(diff), cur)} {'more' if diff > 0 else 'less'} than a typical week ({fmt(typical, cur)})"
             if typical else "")
    body = f"You spent {fmt(spent, cur)} in the last 7 days" + (f", {trend}" if trend else "") + "."
    if top:
        body += f" Biggest category: {top} at {fmt(float(by_cat.iloc[0]), cur)}."
    iso = t.isocalendar()
    return [Notice("digest", f"digest:{iso.year}-W{iso.week:02d}", "Your week in money", body,
                   severity="warn" if typical and diff > 0.25 * typical else "info",
                   ask="Where did my money go in the last 7 days?")]


def remember_recurring(ctx: SkillContext) -> int:
    """Keep a `recurring` memory for every active subscription and bill, so the agent knows them without a tool
    call. Returns how many were created or changed."""
    store, cur, changed = MemoryStore(ctx.db), ctx.settings.currency, 0
    for r in find_recurring(ctx):
        if not r["active"] or r["kind"] == "savings":
            continue
        text = f"Pays {r['merchant']} {fmt(r['amount'], cur)} {r['cadence']} ({r['kind']})."
        _, action = store.upsert("recurring", text, key=f"recurring {r['merchant'].lower()}", confidence=0.9,
                                 importance=0.45, source="detector")
        changed += action != "unchanged"
    return changed


CHECKS = (subscription_notices, budget_notices, anomaly_notices, weekly_digest)


def run_checks(ctx: SkillContext) -> dict[str, Any]:
    """Run every check, store new notices and refresh recurring memories."""
    if count(ctx.db) == 0:
        return {"created": 0, "notices": [], "recurring_memories": 0}
    created = []
    for check in CHECKS:
        try:
            found = check(ctx)
        except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
            log_event(log, "check_failed", check=check.__name__, error=repr(exc))
            continue
        for n in found:
            cur = ctx.db.execute(
                "INSERT OR IGNORE INTO notices(kind, key, title, body, data, created_at) VALUES (?,?,?,?,?,?)",
                [n.kind, n.key, n.title, n.body, dumps({"severity": n.severity, "ask": n.ask, **n.data}), now_iso()])
            if cur.rowcount:
                created.append(n.title)
    recurring = remember_recurring(ctx)
    log_event(log, "proactive_checks", created=len(created), recurring_memories=recurring)
    return {"created": len(created), "notices": created, "recurring_memories": recurring}


def list_notices(ctx: SkillContext, include_dismissed: bool = False) -> list[dict[str, Any]]:
    """Notices for the UI, newest first."""
    rows = ctx.db.all("SELECT * FROM notices" + ("" if include_dismissed else " WHERE dismissed=0")
                      + " ORDER BY id DESC")
    out = []
    for r in rows:
        data = json.loads(r["data"])
        out.append({"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"],
                    "severity": data.get("severity", "info"), "ask": data.get("ask"), "created_at": r["created_at"],
                    "dismissed": bool(r["dismissed"])})
    # Newest day first; within a day, alerts before warnings before information.
    out.sort(key=lambda n: (n["created_at"][:10], -SEVERITY_RANK.get(n["severity"], 0)), reverse=True)
    return out


def dismiss(ctx: SkillContext, notice_id: int) -> bool:
    return ctx.db.execute("UPDATE notices SET dismissed=1 WHERE id=?", [notice_id]).rowcount > 0


def _nice(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"
