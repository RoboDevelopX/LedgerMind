"""Money values and formatting.

Skills return `Money` (a float subclass) for every currency amount. Code and tests treat it as a number; before a
tool result goes to a model, `render_for_model` turns each `Money` into a formatted string such as "₹1,20,000",
so the model copies amounts instead of computing or reformatting them.
"""
from __future__ import annotations

from typing import Any

SYMBOLS = {"INR": "₹", "USD": "$"}


class Money(float):
    """A currency amount. Behaves exactly like a float."""


def money(x: float) -> Money:
    """Round to 2 decimals and mark as money."""
    return Money(round(float(x), 2))


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs) + "," + tail


def fmt(amount: float, currency: str = "INR", decimals: int | None = None) -> str:
    """Format for people: ₹1,20,000 (lakh grouping) or $1,200. Paise/cents only below 1,000 when non-zero."""
    sign = "-" if amount < 0 else ""
    value = abs(float(amount))
    if decimals is None:
        decimals = 2 if value < 1000 and round(value, 2) != round(value) else 0
    whole, _, frac = f"{value:.{decimals}f}".partition(".")
    grouped = _group_indian(whole) if currency == "INR" else f"{int(whole):,}"
    return f"{sign}{SYMBOLS.get(currency, currency + ' ')}{grouped}" + (f".{frac}" if frac else "")


def render_for_model(obj: Any, currency: str) -> Any:
    """Recursively replace Money values with formatted strings."""
    if isinstance(obj, Money):
        return fmt(obj, currency)
    if isinstance(obj, dict):
        return {k: render_for_model(v, currency) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [render_for_model(v, currency) for v in obj]
    return obj
