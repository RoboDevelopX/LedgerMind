"""PII redaction for any text bound for a model.

Covers emails, UPI IDs, card and account numbers (full or masked), IFSC, IBAN, PAN, Aadhaar, phone numbers, and
people's names in UPI transfers or after cue words ("paid Rahul", "my name is ..."). Name detection is a
best-effort heuristic; the stronger guarantee is that raw transaction descriptions are never sent at all.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from ..merchants import CATEGORIES, KNOWN

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("upi_id", re.compile(r"\b[\w.-]{2,}@[a-z]{2,}\b", re.I)),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("ifsc", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("card", re.compile(r"\b(?:[xX*]{4}[ -]?){2,4}\d{4}\b|\b[xX*]{6,}\d{3,4}\b")),
    ("phone", re.compile(r"(?<![\w.])(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}\b"
                         r"|(?<![\w.])\+?1?[ .-]?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")),
]
# Long digit runs (optionally grouped): Aadhaar if 4-4-4, card if Luhn-valid, otherwise an account number.
_DIGITS = re.compile(r"(?<![\w.,₹$])\d(?:[ -]?\d){8,18}(?!\w|[.,]\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _digit_kind(run: str) -> str:
    digits = re.sub(r"\D", "", run)
    if re.fullmatch(r"\d{4}[ -]\d{4}[ -]\d{4}", run):
        return "aadhaar"
    if 13 <= len(digits) <= 19 and _luhn(digits):
        return "card"
    return "account"
_P2P_NAME = re.compile(r"(/P2[PA]/[^/]*/)([^/]+)(/)", re.I)
_SELF_NAME = re.compile(r"\b(my name is|i am called|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
_CUE_NAME = re.compile(r"\b(to|from|paid|pay|owe|owes|lent|borrowed from|with|for|split with)\s+"
                       r"([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b")
_NOT_NAMES = ({m.lower() for _, m, _ in KNOWN if m} | {c.lower() for c in CATEGORIES} | {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "diwali", "christmas",
    "japan", "goa", "india", "the", "rent", "dinner", "lunch", "groceries", "netflix", "spotify", "ledgermind"})


class Redactor:
    """Stateless PII masking. `extra_terms` (e.g. merchants in the ledger) are never treated as names."""

    def __init__(self, extra_terms: Iterable[str] = ()) -> None:
        self.not_names = _NOT_NAMES | {t.lower() for t in extra_terms}

    def redact(self, text: str) -> tuple[str, Counter[str]]:
        counts: Counter[str] = Counter()

        def sub(kind: str, pattern: re.Pattern[str], repl: str, s: str) -> str:
            out, n = pattern.subn(repl, s)
            counts[kind] += n
            return out

        text = sub("name", _P2P_NAME, r"\1[name]\3", text)
        for kind, pattern in _PATTERNS:
            text = sub(kind, pattern, f"[{kind}]", text)

        def digits(m: re.Match[str]) -> str:
            kind = _digit_kind(m.group(0))
            counts[kind] += 1
            return f"[{kind}]"

        text = _DIGITS.sub(digits, text)
        text = sub("name", _SELF_NAME, r"\1 [name]", text)

        def cue(m: re.Match[str]) -> str:
            words = m.group(2)
            if words.lower() in self.not_names or words.split()[0].lower() in self.not_names:
                return m.group(0)
            counts["name"] += 1
            return f"{m.group(1)} [name]"

        text = _CUE_NAME.sub(cue, text)
        return text, +counts
