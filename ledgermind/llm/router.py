"""Model router: picks the Nemotron model for a role, retries, falls back, and logs every call.

Roles come from `config.yaml`:

- `fast`  -> Nemotron 3 Nano: categorisation, memory extraction, consolidation
- `agent` -> Nemotron 3 Super: the tool-calling conversation
- `deep`  -> Nemotron 3 Ultra: monthly review, what-if narration, goal plans, anomaly explanations

Each role is an ordered chain. A model gets `retries` extra attempts on retryable errors (429, 5xx, timeouts),
honouring `Retry-After`; then, or on a non-retryable error, the next model in the chain is tried.
Before any call, messages pass through the privacy hook, which redacts them and records the exact payload.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from typing import Any
from collections.abc import Callable, Iterator

from ..config import ModelConfig, Settings
from ..logs import get_logger, log_event
from .client import ChatRequest, ChatResult, LLMClient, LLMError, StreamEvent

log = get_logger("llm.router")

PrivacyHook = Callable[[ChatRequest], list[dict[str, Any]]]


@dataclass
class CallRecord:
    """One model call as shown in the UI badge and stored in the call log."""

    role: str
    task: str
    model: str
    label: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    ok: bool
    attempts: int
    fallback_from: list[str] = field(default_factory=list)
    error: str | None = None
    mock: bool = False
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AllModelsFailed(RuntimeError):
    """Every model in a role's chain failed. The message is safe to show to the user."""


def call_cost(model: ModelConfig, result: ChatResult) -> float:
    """USD cost of one call from the token usage and the model's per-million prices in config.yaml."""
    return round((result.usage.prompt_tokens * model.price_in
                  + result.usage.completion_tokens * model.price_out) / 1_000_000, 6)


class ModelRouter:
    def __init__(self, settings: Settings, client: LLMClient, *, privacy_hook: PrivacyHook | None = None,
                 sink: Callable[[CallRecord], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.settings = settings
        self.client = client
        self.privacy_hook = privacy_hook
        self.sink = sink
        self.sleep = sleep
        self.calls: list[CallRecord] = []

    @property
    def mock(self) -> bool:
        return type(self.client).__name__ == "MockClient"

    # ------------------------------------------------------------------ helpers
    def _prepare(self, req: ChatRequest) -> ChatRequest:
        if self.privacy_hook is None:
            return req
        return ChatRequest(**{**req.__dict__, "messages": self.privacy_hook(req)})

    def _wait(self, err: LLMError, attempt: int) -> None:
        cfg = self.settings.provider
        delay = err.retry_after if err.retry_after is not None else cfg.backoff_s * (2 ** attempt)
        self.sleep(min(delay, cfg.max_retry_after_s))

    def _record(self, rec: CallRecord) -> CallRecord:
        self.calls.append(rec)
        log_event(log, "llm_call", **rec.to_dict())
        if self.sink is not None:
            self.sink(rec)
        return rec

    def _ok(self, role: str, req: ChatRequest, model: ModelConfig, result: ChatResult, attempts: int,
            failed: list[str]) -> CallRecord:
        return self._record(CallRecord(
            role=role, task=req.task, model=model.id, label=model.label, latency_ms=result.latency_ms,
            prompt_tokens=result.usage.prompt_tokens, completion_tokens=result.usage.completion_tokens,
            cost_usd=call_cost(model, result), ok=True, attempts=attempts, fallback_from=failed, mock=self.mock))

    def _fail(self, role: str, req: ChatRequest, model: ModelConfig, err: LLMError, attempts: int,
              failed: list[str]) -> None:
        self._record(CallRecord(
            role=role, task=req.task, model=model.id, label=model.label, latency_ms=0, prompt_tokens=0,
            completion_tokens=0, cost_usd=0.0, ok=False, attempts=attempts, fallback_from=list(failed),
            error=str(err)[:300], mock=self.mock))

    # ------------------------------------------------------------------ public
    def chat(self, role: str, req: ChatRequest) -> tuple[ChatResult, CallRecord]:
        """Run a request on the role's chain; returns the result and its call record."""
        req = self._prepare(req)
        failed: list[str] = []
        for model in self.settings.chain(role):
            for attempt in range(self.settings.provider.retries + 1):
                try:
                    result = self.client.chat(model, req)
                    return result, self._ok(role, req, model, result, attempt + 1, failed)
                except LLMError as err:
                    if err.retryable and attempt < self.settings.provider.retries:
                        self._wait(err, attempt)
                        continue
                    self._fail(role, req, model, err, attempt + 1, failed)
                    break
            failed.append(model.id)
        raise AllModelsFailed(_friendly(role))

    def stream(self, role: str, req: ChatRequest) -> Iterator[StreamEvent | CallRecord]:
        """Stream on the role's chain. Falls back only before the first visible token; ends with a CallRecord."""
        req = self._prepare(req)
        failed: list[str] = []
        for model in self.settings.chain(role):
            for attempt in range(self.settings.provider.retries + 1):
                emitted = False
                try:
                    for event in self.client.stream(model, req):
                        if event.kind == "done" and event.result is not None:
                            yield event
                            yield self._ok(role, req, model, event.result, attempt + 1, failed)
                            return
                        emitted = True
                        yield event
                except LLMError as err:
                    if emitted:
                        self._fail(role, req, model, err, attempt + 1, failed)
                        raise AllModelsFailed("The model stopped mid-answer. Please try again.") from err
                    if err.retryable and attempt < self.settings.provider.retries:
                        self._wait(err, attempt)
                        continue
                    self._fail(role, req, model, err, attempt + 1, failed)
                    break
            failed.append(model.id)
        raise AllModelsFailed(_friendly(role))


def _friendly(role: str) -> str:
    return (f"I couldn't reach any {role} model on Nebius Token Factory right now (rate limit or outage). "
            "Your data is safe locally; please try again in a moment.")
