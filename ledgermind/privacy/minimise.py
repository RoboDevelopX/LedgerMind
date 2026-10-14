"""Data minimisation for tool results before they reach a model.

Rules
-----
- keys starting with "_" are local-only (UI sees them, models never do)
- row identifiers and raw text (`id`, `transaction_ids`, `description`, `fingerprint`) are dropped
- lists holding transaction-level rows (dicts with a `date`) are capped at `max_rows`
- strict mode: transaction-level rows are removed entirely; only aggregates remain
- `Money` values become formatted strings so the model copies amounts instead of computing them
"""
from __future__ import annotations

from typing import Any

from ..money import render_for_model

DROP_KEYS = {"id", "transaction_ids", "description", "fingerprint", "account"}


def _row_list(value: Any) -> bool:
    """A list that contains at least one transaction-level row (a dict with a `date`)."""
    return isinstance(value, list) and all(isinstance(v, dict) for v in value) and any("date" in v for v in value)


def minimise(result: Any, *, strict: bool, max_rows: int, currency: str,
             path: str = "") -> tuple[Any, list[str]]:
    """Return (model view, list of what was withheld)."""
    withheld: list[str] = []

    def walk(obj: Any, where: str) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                here = f"{where}.{k}" if where else k
                if k.startswith("_") or k in DROP_KEYS:
                    withheld.append(f"{here} (kept local)")
                    continue
                if _row_list(v):
                    if strict:
                        withheld.append(f"{here}: {len(v)} transaction-level rows (strict mode)")
                        out[k] = {"count": len(v), "detail": "withheld in strict privacy mode"}
                        continue
                    if len(v) > max_rows:
                        withheld.append(f"{here}: {len(v) - max_rows} of {len(v)} rows beyond the cap")
                        v = v[:max_rows]
                out[k] = walk(v, here)
            return out
        if isinstance(obj, list):
            return [walk(v, where) for v in obj]
        return obj

    view = walk(result, path)
    return render_for_model(view, currency), withheld
