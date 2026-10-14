"""Retrieval: SQLite FTS5 relevance blended with importance and recency.

score = 0.55 * relevance + 0.30 * importance + 0.15 * recency  (+0.2 if pinned)
Profile memories are always candidates because almost every money question depends on them.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from .store import MemoryStore

W_RELEVANCE, W_IMPORTANCE, W_RECENCY, PIN_BONUS = 0.55, 0.30, 0.15, 0.2
RECENCY_DAYS = 30


def _age_days(ts: str | None) -> float:
    if not ts:
        return 365.0
    then = datetime.fromisoformat(ts)
    return max((datetime.now(UTC) - then).total_seconds() / 86400, 0.0)


def retrieve(store: MemoryStore, query: str, k: int = 8, touch: bool = True) -> list[dict[str, Any]]:
    """Top-k memories for a query, each with its `score`."""
    hits = store.search(query, limit=30)
    relevance: dict[int, float] = {}
    if hits:
        best = max(-h["bm25"] for h in hits) or 1.0
        relevance = {h["id"]: max(-h["bm25"], 0.0) / best for h in hits}
    candidates = {m["id"]: m for m in store.list("profile")}
    candidates.update({m["id"]: m for m in store.list() if m["pinned"]})
    candidates.update({h["id"]: h for h in hits})
    if len(candidates) < k:  # small memory: everything is a candidate
        candidates.update({m["id"]: m for m in store.list()})
    scored = []
    for mid, m in candidates.items():
        recency = math.exp(-_age_days(m.get("last_used_at") or m["updated_at"]) / RECENCY_DAYS)
        score = (W_RELEVANCE * relevance.get(mid, 0.0) + W_IMPORTANCE * m["importance"] + W_RECENCY * recency
                 + (PIN_BONUS if m["pinned"] else 0.0))
        scored.append({**{k_: v for k_, v in m.items() if k_ != "bm25"}, "score": round(score, 3)})
    scored.sort(key=lambda m: -m["score"])
    top = scored[:k]
    if touch:
        store.touch([m["id"] for m in top])
    return top
