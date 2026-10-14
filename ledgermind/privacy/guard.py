"""The privacy hook every model call passes through, and the log behind the Privacy Inspector."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from ..config import Settings
from ..db import Database, dumps, now_iso
from ..llm.client import ChatRequest
from .redact import Redactor


class PrivacyGuard:
    """Redacts outgoing messages, logs the exact payload, and tracks what each turn kept local."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._withheld: dict[str, list[str]] = defaultdict(list)
        self._redactor: Redactor | None = None

    @property
    def strict(self) -> bool:
        return self.settings.privacy.strict_mode

    @property
    def redactor(self) -> Redactor:
        if self._redactor is None:
            merchants = [r["merchant"] for r in self.db.all("SELECT DISTINCT merchant FROM transactions")]
            self._redactor = Redactor(merchants)
        return self._redactor

    def refresh(self) -> None:
        """Forget cached merchant names (call after an import)."""
        self._redactor = None

    def note_withheld(self, turn: str, items: list[str]) -> None:
        """Record what a turn kept local (shown next to that turn's payloads)."""
        self._withheld[turn].extend(items)

    def end_turn(self, turn: str) -> None:
        self._withheld.pop(turn, None)

    # ------------------------------------------------------------- hook
    def __call__(self, req: ChatRequest) -> list[dict[str, Any]]:
        counts: Counter[str] = Counter()
        clean = []
        for m in req.messages:
            m = dict(m)
            if isinstance(m.get("content"), str) and self.settings.privacy.redact:
                m["content"], c = self.redactor.redact(m["content"])
                counts.update(c)
            clean.append(m)
        withheld = list(self._withheld.get(req.turn, []))
        self.db.execute(
            "INSERT INTO privacy_log(ts, turn, task, payload, redactions, withheld, strict) VALUES (?,?,?,?,?,?,?)",
            [now_iso(), req.turn, req.task, dumps(clean), dumps(dict(counts)), dumps(withheld),
             int(self.strict)])
        return clean

    # ------------------------------------------------------------- inspector
    def last_turn(self) -> dict[str, Any]:
        """Every payload of the most recent turn (or the latest call if it had no turn)."""
        last = self.db.one("SELECT * FROM privacy_log ORDER BY id DESC LIMIT 1")
        if last is None:
            return {"calls": [], "strict": self.strict}
        rows = (self.db.all("SELECT * FROM privacy_log WHERE turn=? ORDER BY id", [last["turn"]])
                if last["turn"] else [last])
        calls = [{"id": r["id"], "ts": r["ts"], "task": r["task"], "payload": json.loads(r["payload"]),
                  "redactions": json.loads(r["redactions"]), "withheld": json.loads(r["withheld"]),
                  "strict": bool(r["strict"]), "chars": len(r["payload"])} for r in rows]
        local = int(self.db.scalar("SELECT COUNT(*) FROM transactions") or 0)
        return {"turn": last["turn"], "calls": calls, "strict": self.strict,
                "never_sent": {"transactions_in_local_ledger": local,
                               "raw_descriptions": "never sent; models see normalised merchant names only",
                               "account_identifiers": "never sent"}}
