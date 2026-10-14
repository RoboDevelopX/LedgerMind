"""Date helpers. `today()` honours LEDGERMIND_TODAY so tests and demos are reproducible."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta

PERIOD_HELP = ("this_month | last_month | last_30_days | last_90_days | last_3_months | last_6_months | "
               "last_12_months | this_year | YYYY-MM | YYYY | all. A bare month name such as september means its "
               "most recent occurrence, so prefer it over guessing a year")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")


def _month_number(word: str) -> int | None:
    """1-12 for a month name or its common abbreviation (sep, sept), else None."""
    for i, name in enumerate(_MONTHS, start=1):
        if word == name or (len(word) >= 3 and name.startswith(word) and len(word) <= 4):
            return i
    return None


def today() -> date:
    override = os.getenv("LEDGERMIND_TODAY")
    return date.fromisoformat(override) if override else date.today()


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return (month_start(d) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def add_months(d: date, n: int) -> date:
    """First day of the month `n` months after `d`'s month."""
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


def month_key(d: date) -> str:
    return d.strftime("%Y-%m")


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str
    whole_months: bool = False
    month_to_date: bool = False

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        """The period of the same shape immediately before this one."""
        if self.whole_months:
            months = (self.end.year - self.start.year) * 12 + self.end.month - self.start.month + 1
            start = add_months(self.start, -months)
            end = month_end(add_months(self.start, -1))
            label = start.strftime("%B %Y") if months == 1 else f"{start:%b %Y} to {end:%b %Y}"
            return Period(start, end, label, True)
        if self.month_to_date:
            start = add_months(self.start, -1)
            end = min(start + (self.end - self.start), month_end(start))
            return Period(start, end, f"{start:%B} 1-{end.day}")
        end = self.start - timedelta(days=1)
        return Period(end - timedelta(days=self.days - 1), end, f"the {self.days} days before")

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "start": self.start.isoformat(), "end": self.end.isoformat()}


def resolve_period(period: str | None) -> Period:
    """Parse a period keyword into concrete dates. Unknown values mean this month."""
    t = today()
    p = (period or "this_month").strip().lower().replace(" ", "_")
    if re.fullmatch(r"\d{4}-\d{2}", p):
        start = date(int(p[:4]), int(p[5:]), 1)
        return Period(start, month_end(start), start.strftime("%B %Y"), True)
    if re.fullmatch(r"\d{4}", p):
        start, end = date(int(p), 1, 1), date(int(p), 12, 31)
        return Period(start, min(end, t), p)
    if p == "last_month":
        start = add_months(t, -1)
        return Period(start, month_end(start), start.strftime("%B %Y"), True)
    m = re.fullmatch(r"last_(\d+)_months?", p)
    if m:
        n = int(m.group(1))
        start = add_months(t, -n)
        end = month_end(add_months(t, -1))
        return Period(start, end, f"the last {n} full months ({start:%b %Y} to {end:%b %Y})", True)
    m = re.fullmatch(r"last_(\d+)_days|(\d+)d", p)
    if m:
        n = int(m.group(1) or m.group(2))
        return Period(t - timedelta(days=n - 1), t, f"the last {n} days")
    if p == "this_year":
        return Period(date(t.year, 1, 1), t, f"{t.year} so far")
    if p in ("all", "all_time"):
        return Period(date(1900, 1, 1), t, "all time")
    m = re.fullmatch(r"([a-z]+)(?:_(\d{4}))?", p)
    if m and (num := _month_number(m.group(1))):
        # Models tend to guess a year from their training data, so a bare month resolves against today.
        year = int(m.group(2)) if m.group(2) else (t.year if num <= t.month else t.year - 1)
        start = date(year, num, 1)
        return Period(start, month_end(start), start.strftime("%B %Y"), True)
    return Period(month_start(t), t, t.strftime("%B %Y") + " so far", month_to_date=True)
