"""CSV import: generic bank exports with header auto-detection and optional column mapping.

Bank integrations are deliberately absent: a CSV the user exports themselves is the only way data gets in.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..db import Database, now_iso
from ..ledger import add_transactions, date_range
from ..merchants import CATEGORIES
from . import SkillContext, skill

FIELDS = ("date", "description", "amount", "debit", "credit", "type", "category")
SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("date", "txn date", "transaction date", "tran date", "posting date", "posted date", "posted",
             "booking date", "value date"),
    "description": ("description", "narration", "particulars", "transaction details", "details", "remarks",
                    "merchant", "payee", "name", "memo"),
    "amount": ("amount", "transaction amount", "amount inr", "amount usd", "txn amount", "value"),
    "debit": ("debit", "withdrawal", "withdrawal amt", "withdrawal amount", "debit amount", "dr", "money out",
              "paid out"),
    "credit": ("credit", "deposit", "deposit amt", "deposit amount", "credit amount", "cr", "money in", "paid in"),
    "type": ("type", "dr cr", "cr dr", "transaction type", "debit credit"),
    "category": ("category",),
}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%y", "%d-%b-%Y", "%d-%b-%y",
                "%d %b %Y", "%d %b %y", "%b %d, %Y", "%d.%m.%Y", "%Y/%m/%d")
MAX_BYTES = 5_000_000


class CSVImportError(ValueError):
    """The file could not be read as a bank statement. The message is shown to the user."""


@dataclass
class ParsedCSV:
    rows: list[dict[str, Any]]
    mapping: dict[str, str]
    date_format: str
    header_line: int
    skipped: list[str] = field(default_factory=list)


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()


def detect_columns(headers: list[str]) -> dict[str, str]:
    """Map our fields to the file's column names. Exact synonym matches beat partial ones."""
    normed = {h: _norm(h) for h in headers if h and h.strip()}
    mapping: dict[str, str] = {}
    for fld in FIELDS:
        for exact in (True, False):
            for syn in SYNONYMS[fld]:
                for orig, n in normed.items():
                    if orig in mapping.values():
                        continue
                    if (n == syn) if exact else (syn in n.split() or n.startswith(syn + " ")):
                        mapping[fld] = orig
                        break
                if fld in mapping:
                    break
            if fld in mapping:
                break
    return mapping


def _find_header(lines: list[list[str]]) -> int:
    """Index of the first row that looks like a header (bank exports often start with account details)."""
    for i, row in enumerate(lines[:40]):
        m = detect_columns(row)
        if "date" in m and "description" in m and ("amount" in m or "debit" in m or "credit" in m):
            return i
    raise CSVImportError("I couldn't find a header row with date, description and amount (or debit/credit) "
                         "columns. Check the file is a CSV export of transactions, or pass a column mapping.")


def _split(text: str, mapping: dict[str, str] | None) -> tuple[list[list[str]], int]:
    """Rows and header index, trying each common delimiter until a header row is found."""
    error: CSVImportError | None = None
    for delim in (",", ";", "\t", "|"):
        lines = list(csv.reader(io.StringIO(text), delimiter=delim))
        if mapping:
            idx = next((i for i, r in enumerate(lines[:40]) if set(mapping.values()) <= set(r)), -1)
            if idx >= 0:
                return lines, idx
            error = CSVImportError(f"Columns {sorted(set(mapping.values()))} from your mapping are not in the file.")
            continue
        try:
            return lines, _find_header(lines)
        except CSVImportError as exc:
            error = exc
    raise error or CSVImportError("The file could not be read.")


def parse_amount(raw: str | None) -> float | None:
    """'₹1,234.50', 'Rs. 99', '(45.00)', '1,200 Dr', '-12' -> float. Empty -> None."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or s in ("-", "--"):
        return None
    sign = 1.0
    low = s.lower()
    if low.endswith(("dr", "dr.")):
        sign, s = -1.0, re.sub(r"\s*dr\.?$", "", s, flags=re.I)
    elif low.endswith(("cr", "cr.")):
        s = re.sub(r"\s*cr\.?$", "", s, flags=re.I)
    if s.startswith("(") and s.endswith(")"):
        sign, s = -sign, s[1:-1]
    s = re.sub(r"(?i)inr|usd|rs\.?|[₹$€£,\s]", "", s)
    if s.startswith("-"):
        sign, s = -sign, s[1:]
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise ValueError(f"not an amount: {raw!r}")
    return sign * float(s)


def choose_date_format(values: list[str], dayfirst: bool) -> str:
    """The format that parses every date in the file; ambiguous files use the currency's convention."""
    fits = []
    for fmt in DATE_FORMATS:
        try:
            for v in values:
                datetime.strptime(v.strip(), fmt)
            fits.append(fmt)
        except ValueError:
            continue
    if not fits:
        sample = ", ".join(values[:3])
        raise CSVImportError(f"I couldn't read the dates (e.g. {sample}). Supported: 2026-10-05, 05/10/2026, "
                             "05-Oct-2026 and similar.")
    preferred = [f for f in fits if f.startswith("%d")] if dayfirst else [f for f in fits if f.startswith("%m")]
    return (preferred or fits)[0]


def fingerprint(date: str, description: str, amount: float, account: str, occurrence: int) -> str:
    """Stable id for a row: re-importing a file is a no-op, but genuine duplicate rows in one file both count."""
    raw = f"{date}|{description.strip().lower()}|{amount:.2f}|{account}|{occurrence}"
    return hashlib.sha1(raw.encode()).hexdigest()


def parse_csv(text: str, *, account: str = "default", mapping: dict[str, str] | None = None,
              dayfirst: bool = True) -> ParsedCSV:
    """Parse a bank CSV into normalised rows (date ISO, description, signed amount)."""
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise CSVImportError("The file is empty.")
    lines, header_idx = _split(text, mapping)
    headers = lines[header_idx]
    cols = {**detect_columns(headers), **(mapping or {})}
    if "date" not in cols or "description" not in cols or not ({"amount", "debit", "credit"} & set(cols)):
        raise CSVImportError(f"Missing a date, description or amount column. Columns found: {headers}")
    index = {h: i for i, h in enumerate(headers)}
    body = [r for r in lines[header_idx + 1:] if any(c.strip() for c in r)]

    def cell(row: list[str], fld: str) -> str:
        col = cols.get(fld)
        return row[index[col]].strip() if col in index and index[col] < len(row) else ""

    dated = [r for r in body if cell(r, "date")]
    fmt = choose_date_format([cell(r, "date") for r in dated], dayfirst)
    rows, skipped, seen = [], [], {}
    for r in dated:
        try:
            date = datetime.strptime(cell(r, "date"), fmt).date().isoformat()
            if "amount" in cols:
                amount = parse_amount(cell(r, "amount"))
                kind = cell(r, "type").lower()
                if amount is not None and amount > 0 and kind in ("dr", "debit", "withdrawal", "d"):
                    amount = -amount
            else:
                credit, debit = parse_amount(cell(r, "credit")), parse_amount(cell(r, "debit"))
                amount = None if credit is None and debit is None else (credit or 0.0) - abs(debit or 0.0)
            desc = cell(r, "description")
            if amount is None or not desc:
                raise ValueError("no amount or description")
        except ValueError as exc:
            skipped.append(f"{','.join(r)[:80]} ({exc})")
            continue
        n = seen.get((date, desc, amount), 0)
        seen[(date, desc, amount)] = n + 1
        category = cell(r, "category")
        rows.append({"date": date, "description": desc, "amount": round(amount, 2), "account": account,
                     "category": category if category in CATEGORIES else "Uncategorized",
                     "category_source": "csv" if category in CATEGORIES else "",
                     "fingerprint": fingerprint(date, desc, amount, account, n)})
    if not rows:
        raise CSVImportError("No transactions could be read from the file. " + (skipped[0] if skipped else ""))
    return ParsedCSV(rows, cols, fmt, header_idx, skipped)


def save_upload(db: Database, filename: str, content: str) -> str:
    """Keep an uploaded file locally until it is imported; returns its upload id."""
    if len(content.encode()) > MAX_BYTES:
        raise CSVImportError("The file is larger than 5 MB.")
    upload_id = secrets.token_hex(6)
    db.execute("INSERT INTO uploads(id, filename, content, created_at) VALUES (?,?,?,?)",
               [upload_id, filename, content, now_iso()])
    return upload_id


def import_text(ctx: SkillContext, text: str, *, account: str = "default",
                mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """Parse, store and categorise a CSV. Shared by the upload endpoint, the demo loader and the skill."""
    from .categorize import categorize_pending, prepare_rows

    parsed = parse_csv(text, account=account, mapping=mapping, dayfirst=ctx.settings.currency != "USD")
    new_ids = add_transactions(ctx.db, prepare_rows(ctx.db, parsed.rows))
    categorized = categorize_pending(ctx)
    lo, hi = date_range(ctx.db)
    return {"rows_read": len(parsed.rows), "added": len(new_ids),
            "already_imported": len(parsed.rows) - len(new_ids), "skipped_rows": len(parsed.skipped),
            "_skipped_examples": parsed.skipped[:3], "columns": parsed.mapping, "date_format": parsed.date_format,
            "categorized": categorized, "ledger_covers": {"from": lo, "to": hi}}


@skill("import_transactions",
       "Import a bank CSV the user uploaded (by upload_id). Detects the header row and columns automatically; "
       "column_mapping is only needed if detection fails.",
       {"upload_id": {"type": "string", "description": "id returned when the file was uploaded"},
        "column_mapping": {"type": "object", "description": "optional {field: column name}; fields: "
                           + ", ".join(FIELDS)},
        "account": {"type": "string", "description": "account label, e.g. 'HDFC savings'"}},
       ("upload_id",), mutates=True)
def import_transactions(ctx: SkillContext, upload_id: str, column_mapping: dict[str, str] | None = None,
                        account: str = "default") -> dict[str, Any]:
    """Import a previously uploaded CSV. Raw rows stay in the local database; nothing is sent to a model."""
    row = ctx.db.one("SELECT * FROM uploads WHERE id=?", [upload_id])
    if row is None:
        return {"error": f"no uploaded file with id {upload_id}. Ask the user to upload the CSV first."}
    try:
        result = import_text(ctx, row["content"], account=account, mapping=column_mapping)
    except CSVImportError as exc:
        return {"error": str(exc)}
    ctx.db.execute("DELETE FROM uploads WHERE id=?", [upload_id])
    return {"file": row["filename"], **result}
