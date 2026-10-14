"""Typed long-term memory with dedupe and merge.

Types
-----
profile     income, currency, household, payday (one memory per `key`, newest value wins)
goal        savings goals in the user's words (numbers live in the goals table)
habit       patterns ("orders food delivery most Fridays")
recurring   subscriptions and bills with cadence
preference  how the user wants advice ("prefers short answers", "vegetarian, no meat deals")
correction  the user fixed a category: never repeat that mistake
decision    past advice and its outcome ("decided to cancel Apple Music in Oct 2026")
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..db import Database, dumps, now_iso

MEMORY_TYPES = ("profile", "goal", "habit", "recurring", "preference", "correction", "decision")

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "be", "user", "users",
         "their", "they", "has", "have", "with", "at", "by", "it", "that", "this", "per", "every", "each", "s"}
MERGE_SIMILARITY = 0.75


def tokens(text: str) -> set[str]:
    """Content words, lowercased, for similarity and keys."""
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of content words."""
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def amounts(text: str) -> set[str]:
    """Numbers in `text` with grouping commas removed ("₹3,00,000" -> "300000")."""
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def refines(old: str, new: str) -> bool:
    """Whether `new` may replace a user-confirmed `old`: it states a different amount or date, or keeps every word
    of `old`. A model's looser paraphrase ("Target ₹3,00,000" for "Saving for Japan: target ₹3,00,000") does not."""
    return amounts(new) != amounts(old) or tokens(old) <= tokens(new)


def make_key(content: str) -> str:
    return " ".join(sorted(tokens(content))[:8]) or content.strip().lower()[:60]


def _row(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if r is None:
        return None
    r = dict(r)
    r["data"] = json.loads(r.get("data") or "{}")
    r["pinned"] = bool(r.get("pinned"))
    return r


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ----------------------------------------------------------------- write
    def upsert(self, type: str, content: str, *, key: str | None = None, data: dict[str, Any] | None = None,
               confidence: float = 0.8, importance: float = 0.5, source: str = "chat") -> tuple[dict[str, Any], str]:
        """Add a memory, or merge it into an existing one with the same key or very similar content.

        Returns (memory, action) where action is "created", "merged" or "unchanged".
        """
        if type not in MEMORY_TYPES:
            raise ValueError(f"unknown memory type {type!r}")
        content = re.sub(r"\s+", " ", content).strip()
        if not content:
            raise ValueError("empty memory")
        key = (key or make_key(content)).strip().lower()
        existing = self._find(type, key, content)
        now = now_iso()
        if existing is None:
            cur = self.db.execute(
                "INSERT INTO memories(type, key, content, data, confidence, importance, source, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                [type, key, content, dumps(data or {}), confidence, importance, source, now, now])
            return self.get(int(cur.lastrowid)), "created"  # type: ignore[return-value]
        same = existing["content"] == content and (data or {}) == {k: v for k, v in existing["data"].items()
                                                                    if k != "history"}
        if same or (existing["source"] == "user" and source == "chat" and not refines(existing["content"], content)):
            # A model's paraphrase never overwrites what the user said or confirmed; it only refreshes it.
            self.db.execute("UPDATE memories SET confidence=?, updated_at=? WHERE id=?",
                            [max(existing["confidence"], confidence), now, existing["id"]])
            return self.get(existing["id"]), "unchanged"  # type: ignore[return-value]
        merged = {**existing["data"], **(data or {})}
        if existing["content"] != content:
            merged["history"] = ([existing["content"]] + existing["data"].get("history", []))[:3]
        self.db.execute(
            "UPDATE memories SET content=?, data=?, confidence=?, importance=?, source=?, updated_at=? WHERE id=?",
            [content, dumps(merged), max(confidence, existing["confidence"] * 0.9),
             max(importance, existing["importance"]), source, now, existing["id"]])
        return self.get(existing["id"]), "merged"  # type: ignore[return-value]

    def _find(self, type: str, key: str, content: str) -> dict[str, Any] | None:
        hit = _row(self.db.one("SELECT * FROM memories WHERE type=? AND key=?", [type, key]))
        if hit:
            return hit
        best, best_sim = None, MERGE_SIMILARITY
        for m in self.list(type):
            sim = similarity(m["content"], content)
            if sim >= best_sim:
                best, best_sim = m, sim
        return best

    def update(self, memory_id: int, *, content: str | None = None, importance: float | None = None,
               pinned: bool | None = None, type: str | None = None) -> dict[str, Any] | None:
        """User edit from the memory panel."""
        current = self.get(memory_id)
        if current is None:
            return None
        if type is not None and type not in MEMORY_TYPES:
            raise ValueError(f"unknown memory type {type!r}")
        self.db.execute(
            "UPDATE memories SET content=?, importance=?, pinned=?, type=?, source='user', confidence=1.0,"
            " updated_at=? WHERE id=?",
            [content.strip() if content else current["content"],
             current["importance"] if importance is None else importance,
             int(current["pinned"] if pinned is None else pinned), type or current["type"], now_iso(), memory_id])
        return self.get(memory_id)

    def delete(self, memory_id: int) -> bool:
        return self.db.execute("DELETE FROM memories WHERE id=?", [memory_id]).rowcount > 0

    def touch(self, ids: list[int]) -> None:
        """Mark memories as used in a turn (feeds recency scoring)."""
        if ids:
            marks = ",".join("?" * len(ids))
            self.db.execute(f"UPDATE memories SET last_used_at=?, use_count=use_count+1 WHERE id IN ({marks})",
                            [now_iso(), *ids])

    # ----------------------------------------------------------------- read
    def get(self, memory_id: int) -> dict[str, Any] | None:
        return _row(self.db.one("SELECT * FROM memories WHERE id=?", [memory_id]))

    def list(self, type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories" + (" WHERE type=?" if type else "") + " ORDER BY type, importance DESC, id"
        return [_row(r) for r in self.db.all(sql, [type] if type else [])]  # type: ignore[misc]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM memories") or 0)

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search (FTS5, BM25). Returns memories with a `bm25` score (lower is better)."""
        terms = sorted(tokens(query))
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        rows = self.db.all(
            "SELECT m.*, bm25(memories_fts) AS bm25 FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid"
            " WHERE memories_fts MATCH ? ORDER BY bm25 LIMIT ?", [match, limit])
        return [_row(r) for r in rows]  # type: ignore[misc]

    def corrections(self) -> dict[str, str]:
        """merchant name or alias (lowercase) -> category the user chose."""
        out: dict[str, str] = {}
        for m in self.list("correction"):
            if "category" in m["data"]:
                for name in [m["key"], *m["data"].get("aliases", [])]:
                    out[name] = m["data"]["category"]
        return out
