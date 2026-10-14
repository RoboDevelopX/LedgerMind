"""The local ledger: one SQLite file the user owns. Nothing in it leaves the machine.

Tables
------
transactions   imported bank rows (raw descriptions stay here)
memories       typed long-term memory, with an FTS5 index for retrieval
goals, budgets savings goals and monthly category limits
messages       conversation log
llm_calls      every model call: model, latency, tokens, cost
privacy_log    the exact (redacted) payload of every model call and what was withheld
notices        proactive "Ledgermind noticed…" cards
reviews        generated monthly reviews (for Markdown export)
uploads        CSV files waiting to be imported
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, UTC
from typing import Any
from collections.abc import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                       -- ISO yyyy-mm-dd
    description TEXT NOT NULL,                -- raw bank text, never sent to a model
    amount REAL NOT NULL,                     -- negative = money out
    merchant TEXT NOT NULL DEFAULT '',        -- normalised merchant name
    category TEXT NOT NULL DEFAULT 'Uncategorized',
    category_source TEXT NOT NULL DEFAULT '', -- csv | rule | correction | model | user | fallback
    account TEXT NOT NULL DEFAULT 'default',
    fingerprint TEXT NOT NULL UNIQUE,         -- makes re-importing the same file a no-op
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_tx_merchant ON transactions(merchant);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,                       -- profile | goal | habit | recurring | preference | correction | decision
    key TEXT NOT NULL,                        -- normalised dedupe key
    content TEXT NOT NULL,                    -- one sentence, third person
    data TEXT NOT NULL DEFAULT '{}',          -- structured fields (JSON)
    confidence REAL NOT NULL DEFAULT 0.8,
    importance REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL DEFAULT 'chat',      -- chat | user | detector | consolidation
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    UNIQUE(type, key)
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, key, type, content='memories', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, key, type) VALUES (new.id, new.content, new.key, new.type);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, key, type)
    VALUES ('delete', old.id, old.content, old.key, old.type);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, key, type)
    VALUES ('delete', old.id, old.content, old.key, old.type);
    INSERT INTO memories_fts(rowid, content, key, type) VALUES (new.id, new.content, new.key, new.type);
END;

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    target REAL NOT NULL,
    saved REAL NOT NULL DEFAULT 0,
    deadline TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS privacy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    turn TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL,
    payload TEXT NOT NULL,                    -- exact messages sent, after redaction
    redactions TEXT NOT NULL DEFAULT '{}',    -- counts by kind
    withheld TEXT NOT NULL DEFAULT '[]',      -- what stayed local
    strict INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    key TEXT NOT NULL UNIQUE,                 -- dedupe: one card per event
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reviews (
    month TEXT PRIMARY KEY,                   -- YYYY-MM
    data TEXT NOT NULL,                       -- computed figures (JSON)
    report TEXT NOT NULL,                     -- narrative: headline, wins, risks, actions (JSON)
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    """UTC timestamp, second precision."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: str = "ledgermind.db") -> None:
        self.path = path
        _move_aside_v1(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> int:
        with self._lock:
            cur = self.conn.executemany(sql, [tuple(r) for r in rows])
            self.conn.commit()
            return cur.rowcount

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self._lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------- user control
    def export(self) -> dict[str, Any]:
        """Everything Ledgermind stores, as plain JSON-able data."""
        tables = ("transactions", "memories", "goals", "budgets", "messages", "notices", "reviews", "llm_calls",
                  "privacy_log")
        return {"exported_at": now_iso(), **{t: self.all(f"SELECT * FROM {t} ORDER BY rowid") for t in tables}}

    def wipe(self) -> None:
        """Delete all user data (keeps the schema)."""
        with self._lock:
            for t in ("transactions", "memories", "goals", "budgets", "messages", "llm_calls", "privacy_log",
                      "notices", "reviews", "uploads"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def _move_aside_v1(path: str) -> None:
    """A v1 ledger has an incompatible schema: keep it as <name>.v1 and start fresh."""
    if path == ":memory:" or not os.path.exists(path):
        return
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    finally:
        conn.close()
    if cols and "fingerprint" not in cols:
        os.replace(path, path + ".v1")


def dumps(obj: Any) -> str:
    """Compact JSON for storage."""
    return json.dumps(obj, default=str, ensure_ascii=False)
