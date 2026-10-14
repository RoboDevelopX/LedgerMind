"""Transaction storage and the pandas view every skill computes from."""
from __future__ import annotations

from datetime import date
from typing import Any
from collections.abc import Iterable

import pandas as pd

from .db import Database, now_iso

COLUMNS = ["id", "date", "description", "amount", "merchant", "category", "category_source", "account"]


def add_transactions(db: Database, rows: Iterable[dict[str, Any]]) -> list[int]:
    """Insert rows (each with a `fingerprint`); rows already present are skipped. Returns new ids."""
    new_ids = []
    with db._lock:
        for r in rows:
            cur = db.conn.execute(
                "INSERT OR IGNORE INTO transactions(date, description, amount, merchant, category, category_source,"
                " account, fingerprint, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (r["date"], r["description"], float(r["amount"]), r.get("merchant", ""),
                 r.get("category") or "Uncategorized", r.get("category_source", ""),
                 r.get("account") or "default", r["fingerprint"], now_iso()))
            if cur.rowcount:
                new_ids.append(int(cur.lastrowid))
        db.conn.commit()
    return new_ids


def transactions(db: Database, start: date | None = None, end: date | None = None,
                 limit: int | None = None) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM transactions WHERE 1=1", []
    if start:
        sql += " AND date >= ?"
        params.append(start.isoformat())
    if end:
        sql += " AND date <= ?"
        params.append(end.isoformat())
    sql += " ORDER BY date DESC, id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.all(sql, params)


def frame(db: Database, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Transactions as a DataFrame with a datetime `date` column and a `month` (YYYY-MM) column."""
    rows = transactions(db, start, end)
    df = pd.DataFrame(rows, columns=COLUMNS) if rows else pd.DataFrame(columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    df["amount"] = df["amount"].astype(float)
    df["month"] = df["date"].dt.strftime("%Y-%m")
    return df


def uncategorized(db: Database) -> list[dict[str, Any]]:
    return db.all("SELECT * FROM transactions WHERE category='Uncategorized' ORDER BY date")


def set_category(db: Database, tx_id: int, category: str, source: str, merchant: str | None = None) -> None:
    if merchant:
        db.execute("UPDATE transactions SET category=?, category_source=?, merchant=? WHERE id=?",
                   [category, source, merchant, tx_id])
    else:
        db.execute("UPDATE transactions SET category=?, category_source=? WHERE id=?", [category, source, tx_id])


def date_range(db: Database) -> tuple[str | None, str | None]:
    row = db.one("SELECT MIN(date) AS lo, MAX(date) AS hi FROM transactions") or {}
    return row.get("lo"), row.get("hi")


def count(db: Database) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM transactions") or 0)
