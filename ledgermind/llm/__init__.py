"""LLM access: Token Factory client, offline mock, and the config-driven model router."""
from __future__ import annotations

from collections.abc import Callable

from ..config import Settings
from .client import (Brain, ChatRequest, ChatResult, LLMClient, LLMError, MockClient, StreamEvent,
                     TokenFactoryClient, ToolCall, Usage)
from .router import AllModelsFailed, CallRecord, ModelRouter, PrivacyHook

__all__ = ["AllModelsFailed", "Brain", "CallRecord", "ChatRequest", "ChatResult", "LLMClient", "LLMError",
           "MockClient", "ModelRouter", "StreamEvent", "TokenFactoryClient", "ToolCall", "Usage", "make_router"]


def make_router(settings: Settings, brain: Brain, *, privacy_hook: PrivacyHook | None = None,
                sink: Callable[[CallRecord], None] | None = None) -> ModelRouter:
    """Router on Token Factory when a key is configured, otherwise on the offline mock `brain`."""
    client: LLMClient = MockClient(brain) if settings.mock else TokenFactoryClient(settings)
    return ModelRouter(settings, client, privacy_hook=privacy_hook, sink=sink)
