"""Verify the Nebius Token Factory key and every Nemotron model in config.yaml with one real call each.

    python scripts/check_models.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledgermind.config import get_settings, missing_key_help  # noqa: E402
from ledgermind.llm import ChatRequest, LLMError, TokenFactoryClient  # noqa: E402


def main() -> int:
    settings = get_settings()
    if not settings.api_key:
        print(missing_key_help())
        return 1
    client = TokenFactoryClient(settings)
    available = client.list_models()
    print("Nemotron models your key can reach:")
    for model_id in available:
        if "nemotron" in model_id.lower():
            print("  ", model_id)

    ok = True
    for alias, model in settings.models.items():
        if model.id not in available:
            ok = False
            close = [m for m in available if m.lower() == model.id.lower()]
            hint = f"IDs are case-sensitive; use {close[0]}" if close else "pick an ID from the list above"
            print(f"\n[{alias}] {model.id}: NOT AVAILABLE. {hint} (LEDGERMIND_MODEL_{alias.upper()} in .env)")
            continue
        try:
            res = client.chat(model, ChatRequest(messages=[{"role": "user", "content": "Reply with one word: ready"}],
                                                 task="check", max_tokens=200))
            print(f"\n[{alias}] {model.id}: {res.content[:40]!r} in {res.latency_ms} ms "
                  f"({res.usage.prompt_tokens} in / {res.usage.completion_tokens} out tokens)")
            ok = ok and bool(res.content)
        except LLMError as err:
            ok = False
            print(f"\n[{alias}] {model.id}: call failed: {err}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
