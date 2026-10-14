"""Application state: one ledger database plus the router and services built around it."""
from __future__ import annotations

from typing import Any

from .config import Settings, get_settings
from .db import Database, dumps
from .demo import generate, to_csv
from .llm import CallRecord, make_router
from .llm.mock import mock_brain
from .memory.store import MemoryStore
from .privacy.guard import PrivacyGuard
from .skills import SkillContext
from .skills.importer import import_text


class AppState:
    """Everything one user's Ledgermind needs, wired together."""

    def __init__(self, settings: Settings | None = None, db_path: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.db = Database(db_path or self.settings.db_path)
        self.privacy = PrivacyGuard(self.db, self.settings)
        self.memory = MemoryStore(self.db)
        self.router = make_router(self.settings, mock_brain, privacy_hook=self.privacy, sink=self._log_call)

    @property
    def ctx(self) -> SkillContext:
        return SkillContext(self.db, self.settings, self.router)

    def _log_call(self, rec: CallRecord) -> None:
        self.db.execute(
            "INSERT INTO llm_calls(ts, role, task, model, latency_ms, prompt_tokens, completion_tokens, cost_usd, ok,"
            " detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [rec.ts, rec.role, rec.task, rec.model, rec.latency_ms, rec.prompt_tokens, rec.completion_tokens,
             rec.cost_usd, int(rec.ok), dumps({"attempts": rec.attempts, "fallback_from": rec.fallback_from,
                                               "error": rec.error, "mock": rec.mock, "label": rec.label})])

    def load_demo(self, reset: bool = False) -> dict[str, Any]:
        """Import the seeded 12-month demo statement in the configured currency."""
        if reset:
            self.db.wipe()
        result = import_text(self.ctx, to_csv(generate(self.settings.currency), self.settings.currency),
                             account="Demo savings account")
        self.privacy.refresh()
        return result


def build_app_state(db_path: str | None = None) -> AppState:
    return AppState(db_path=db_path)
