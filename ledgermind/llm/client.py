"""Thin chat client interface with two implementations.

- `TokenFactoryClient`: the official `openai` SDK pointed at Nebius Token Factory (OpenAI-compatible).
- `MockClient`: a deterministic offline stand-in so the whole app and its tests run without network.

Both speak the same small dataclasses, so the router and agent never touch SDK objects.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from collections.abc import Callable, Iterator

from ..config import ModelConfig, Settings
from .jsonutil import ThinkFilter, strip_reasoning


@dataclass
class ToolCall:
    """One function call requested by the model; `arguments` is the raw JSON string."""

    id: str
    name: str
    arguments: str


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class ChatRequest:
    """Provider-neutral chat request. `task` names the job for logs, routing and the mock."""

    messages: list[dict[str, Any]]
    task: str = "chat"
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    json_mode: bool = False
    max_tokens: int = 1024
    temperature: float = 0.3
    turn: str = ""            # conversation turn id, for the privacy log


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0


@dataclass
class StreamEvent:
    """`kind` is "text" (a visible answer delta) or "done" (carries the final `result`)."""

    kind: str
    text: str = ""
    result: ChatResult | None = None


class LLMError(Exception):
    """A provider failure. `retryable` errors are retried on the same model; others fall through the chain."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


class LLMClient(Protocol):
    """What the router needs from a provider."""

    def chat(self, model: ModelConfig, req: ChatRequest) -> ChatResult: ...

    def stream(self, model: ModelConfig, req: ChatRequest) -> Iterator[StreamEvent]: ...


# --------------------------------------------------------------------------- Token Factory

class TokenFactoryClient:
    """Nebius Token Factory via the `openai` SDK. Retries are owned by the router, so the SDK's are disabled."""

    def __init__(self, settings: Settings, sdk: Any | None = None) -> None:
        if sdk is None:
            from openai import OpenAI

            sdk = OpenAI(base_url=settings.provider.base_url, api_key=settings.api_key,
                         timeout=settings.provider.timeout_s, max_retries=0)
        self._sdk = sdk

    def list_models(self) -> list[str]:
        """Model IDs this key can reach (`GET /v1/models`)."""
        return sorted(m.id for m in self._sdk.models.list().data)

    @staticmethod
    def _kwargs(model: ModelConfig, req: ChatRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model.id,
            "messages": req.messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            # Nemotron 3 reasons by default; the model card's switch is a chat-template kwarg.
            "extra_body": {"chat_template_kwargs": {"enable_thinking": model.thinking}},
        }
        if req.tools:
            kwargs["tools"] = req.tools
            kwargs["tool_choice"] = req.tool_choice or "auto"
        if req.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def _create(self, kwargs: dict[str, Any]) -> Any:
        try:
            return self._sdk.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to LLMError below
            err = _map_error(exc)
            if err.status == 400 and ("extra_body" in kwargs or "response_format" in kwargs):
                # Some deployments reject optional switches; try once without them.
                kwargs = {k: v for k, v in kwargs.items() if k not in ("extra_body", "response_format")}
                try:
                    return self._sdk.chat.completions.create(**kwargs)
                except Exception as exc2:  # noqa: BLE001
                    raise _map_error(exc2) from exc2
            raise err from exc

    def chat(self, model: ModelConfig, req: ChatRequest) -> ChatResult:
        kwargs = self._kwargs(model, req)
        start = time.monotonic()
        resp = self._create(kwargs)
        choice = resp.choices[0]
        content = strip_reasoning(choice.message.content)
        calls = _tool_calls(getattr(choice.message, "tool_calls", None))
        if not content and not calls and choice.finish_reason == "length":
            # The whole budget went on reasoning: give the model room to finish its answer.
            kwargs["max_tokens"] = req.max_tokens * 4
            resp = self._create(kwargs)
            choice = resp.choices[0]
            content = strip_reasoning(choice.message.content)
            calls = _tool_calls(getattr(choice.message, "tool_calls", None))
        return ChatResult(content=content, tool_calls=calls, finish_reason=choice.finish_reason or "stop",
                          model=model.id, usage=_usage(getattr(resp, "usage", None)),
                          latency_ms=int((time.monotonic() - start) * 1000))

    def stream(self, model: ModelConfig, req: ChatRequest) -> Iterator[StreamEvent]:
        kwargs = self._kwargs(model, req) | {"stream": True, "stream_options": {"include_usage": True}}
        start = time.monotonic()
        chunks = self._create(kwargs)
        think = ThinkFilter()
        text: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish, usage = "stop", Usage()
        try:
            for chunk in chunks:
                if getattr(chunk, "usage", None):
                    usage = _usage(chunk.usage)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if getattr(delta, "content", None):
                    visible = think.feed(delta.content)
                    if visible:
                        text.append(visible)
                        yield StreamEvent("text", visible)
                for tc in getattr(delta, "tool_calls", None) or []:
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function is not None:
                        slot["name"] += tc.function.name or ""
                        slot["arguments"] += tc.function.arguments or ""
                finish = choice.finish_reason or finish
        except Exception as exc:  # noqa: BLE001 - surfaced as LLMError
            raise _map_error(exc) from exc
        tail = think.flush()
        if tail:
            text.append(tail)
            yield StreamEvent("text", tail)
        result = ChatResult(
            content="".join(text).strip(),
            tool_calls=[ToolCall(c["id"] or f"call_{i}", c["name"], c["arguments"] or "{}")
                        for i, c in sorted(calls.items())],
            finish_reason=finish, model=model.id, usage=usage,
            latency_ms=int((time.monotonic() - start) * 1000))
        yield StreamEvent("done", result=result)


def _tool_calls(raw: Any) -> list[ToolCall]:
    return [ToolCall(tc.id, tc.function.name, tc.function.arguments or "{}") for tc in raw or []]


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    details = getattr(raw, "completion_tokens_details", None)
    return Usage(prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
                 completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
                 reasoning_tokens=(getattr(details, "reasoning_tokens", 0) or 0) if details else 0)


def _map_error(exc: Exception) -> LLMError:
    """Classify SDK exceptions without importing the SDK's types (keeps mock mode import-free)."""
    if isinstance(exc, LLMError):
        return exc
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    retry_after = None
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            retry_after = float(response.headers.get("retry-after"))
        except (TypeError, ValueError):
            retry_after = None
    if status == 429 or name == "RateLimitError":
        return LLMError(f"rate limited: {exc}", retryable=True, status=429, retry_after=retry_after)
    if name in ("APITimeoutError", "APIConnectionError") or "timeout" in name.lower():
        return LLMError(f"network: {exc}", retryable=True, status=status)
    if status is not None and status >= 500:
        return LLMError(f"provider error {status}: {exc}", retryable=True, status=status)
    return LLMError(f"{name}: {exc}", retryable=False, status=status)


# --------------------------------------------------------------------------- Mock

Brain = Callable[[ChatRequest], ChatResult]

# Rough per-model latencies so the UI badges and the router logs look realistic offline.
_MOCK_LATENCY_MS = {"nano": 180, "lightning": 160, "super": 650, "ultra": 1400}


class MockClient:
    """Offline client. `brain` produces replies; `failures` maps model ids to errors for testing fallbacks."""

    def __init__(self, brain: Brain, failures: dict[str, list[LLMError]] | None = None) -> None:
        self.brain = brain
        self.failures = failures or {}
        self.requests: list[tuple[str, ChatRequest]] = []

    def _maybe_fail(self, model: ModelConfig) -> None:
        queue = self.failures.get(model.id)
        if queue:
            raise queue.pop(0)

    def chat(self, model: ModelConfig, req: ChatRequest) -> ChatResult:
        self.requests.append((model.id, req))
        self._maybe_fail(model)
        result = self.brain(req)
        prompt = sum(len(json.dumps(m.get("content") or "")) for m in req.messages) // 4
        result.model = model.id
        result.usage = Usage(prompt_tokens=prompt, completion_tokens=max(len(result.content) // 4, 1))
        alias = next((k for k in _MOCK_LATENCY_MS if k in model.id.lower()), "nano")
        result.latency_ms = _MOCK_LATENCY_MS[alias]
        return result

    def stream(self, model: ModelConfig, req: ChatRequest) -> Iterator[StreamEvent]:
        result = self.chat(model, req)
        words = result.content.split(" ") if result.content else []
        for i, word in enumerate(words):
            yield StreamEvent("text", word + (" " if i < len(words) - 1 else ""))
        yield StreamEvent("done", result=result)
