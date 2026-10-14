"""Number guard: every figure in a model's answer must come from a tool result, memory or the user.

The model is told never to do arithmetic on money. This check enforces it after the fact: numbers in the answer
are compared with numbers in the sources (formatted amounts are normalised, so "₹1,20,000" matches 120000).
A value counts as supported if it equals a source number, or is that number rounded to fewer significant digits
("about ₹26,500" for 26,519). Anything else is reported so the agent can regenerate the answer.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_NUM = re.compile(r"(?<![\w.])[-−]?(?:[₹$]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|k\b|lakh|lakhs|l\b|cr|crore|crores))?",
                  re.I)
_MULT = {"k": 1e3, "lakh": 1e5, "lakhs": 1e5, "l": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7}
# Plain small integers are counts, ranks and list numbering ("3 actions", "top 5"); plain 19xx-21xx are years.
SMALL_INT_MAX = 31


@dataclass
class GuardResult:
    ok: bool
    unsupported: list[str] = field(default_factory=list)
    checked: int = 0


def parse_numbers(text: str) -> list[tuple[str, float]]:
    """(original text, value) for every number-like token."""
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0)
        token = raw.lower().replace("−", "-").replace("₹", "").replace("$", "").replace(",", "").strip()
        mult = 1.0
        for suffix, factor in _MULT.items():
            if re.search(rf"\s?{suffix}$", token):
                token, mult = re.sub(rf"\s?{suffix}$", "", token), factor
                break
        token = token.rstrip("%").strip()
        try:
            out.append((raw.strip(), float(token) * mult))
        except ValueError:
            continue
    return out


def _flatten(obj: Any) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _flatten(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flatten(v)
    elif obj is not None:
        yield str(obj)


def source_numbers(*sources: Any) -> set[float]:
    """Every number appearing in the given strings or JSON-like structures."""
    values: set[float] = set()
    for src in sources:
        text = src if isinstance(src, str) else " ".join(_flatten(src)) + " " + json.dumps(src, default=str)
        for _, v in parse_numbers(text):
            values.add(round(abs(v), 2))
    return values


def _supported(raw: str, value: float, allowed: set[float]) -> bool:
    v = round(abs(value), 2)
    plain = not re.search(r"[%₹$]|k$|lakh|l$|cr", raw.lower())
    if v in allowed or (plain and ((v.is_integer() and v <= SMALL_INT_MAX) or 1900 <= v <= 2100)):
        return True
    for a in allowed:
        if a == 0:
            continue
        # Rounded presentation of a source value (e.g. 26519 -> 26500, 23.08 -> 23), within 2%.
        if abs(v - a) / a <= 0.02 and len(str(int(v)).rstrip("0")) <= len(str(int(a)).rstrip("0")):
            return True
    return False


def check(answer: str, *sources: Any) -> GuardResult:
    """Check an answer against its sources."""
    allowed = source_numbers(*sources)
    found = parse_numbers(answer)
    bad = [raw for raw, value in found if not _supported(raw, value, allowed)]
    return GuardResult(ok=not bad, unsupported=sorted(set(bad)), checked=len(found))
