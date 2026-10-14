"""After each turn, Nemotron Nano proposes candidate memories as JSON; they are validated, then merged into the
store (dedupe by key and similarity)."""
from __future__ import annotations

import re
from typing import Any

from ..config import Settings
from ..llm import AllModelsFailed, ChatRequest, ModelRouter
from ..llm.jsonutil import parse_json
from ..logs import get_logger, log_event
from .store import MEMORY_TYPES, MERGE_SIMILARITY, MemoryStore, similarity, tokens

log = get_logger("memory")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SCALE = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7,
          "crores": 1e7, "cr": 1e7, "million": 1e6, "mn": 1e6}

EXTRACT_PROMPT = """You maintain long-term memory for a private personal-finance assistant.
From the user's latest message (the assistant's reply is context only), extract facts worth remembering for months.
Types:
- profile: income, payday, household, city, currency, employment (key = that field name)
- goal: something they are saving for (key = goal name)
- habit: a spending pattern they describe
- recurring: a bill or subscription they mention
- preference: how they want advice, or lifestyle constraints that affect money
- correction: they say a category or fact you used is wrong
- decision: something they decided to do with their money
Rules: only facts the user states about themselves in USER. Never take facts from the ASSISTANT text, and never
infer a city, employer, goal or preference they did not state. Questions ("how much did I spend...") are not
facts or preferences: skip them, along with greetings and one-off chatter. Skip anything already in KNOWN unless
it changed. Copy numbers exactly from the user's words, never compute or invent them. Write each memory as one
short third-person sentence ("Gets paid on the last working day of the month.").
Reply with JSON only: {"memories": [{"type": "...", "key": "...", "content": "...", "confidence": 0.0-1.0,
"importance": 0.0-1.0}]}. Use {"memories": []} when nothing qualifies."""


def _numbers(text: str, scaled: bool = False) -> set[float]:
    """Numbers in `text`. With `scaled`, "50k" and "3 lakh" also count as 50000 and 300000."""
    found: set[float] = set()
    for m in _NUMBER.finditer(text):
        try:
            value = float(m.group().replace(",", ""))
        except ValueError:
            continue
        found.add(value)
        unit = re.match(r"\s*([a-z]+)", text[m.end():].lower()) if scaled else None
        if unit and unit.group(1) in _SCALE:
            found.add(value * _SCALE[unit.group(1)])
    return found


def _stems(text: str) -> set[str]:
    return {t[:4] for t in tokens(text) if not t.isdigit()}


def grounded(content: str, user_text: str) -> bool:
    """Whether a candidate memory comes from the user's own words, not the assistant's reply or a guess.

    It must share a content word with the user's message, and every amount in it (ignoring dates, years and small
    numbers such as "2 kids" or "the 1st") must appear there too.
    """
    if not _stems(content) & _stems(user_text):
        return False
    said = _numbers(user_text, scaled=True)
    for m in _NUMBER.finditer(_DATE.sub(" ", content)):
        raw = m.group()
        value = float(raw.replace(",", ""))
        is_year = len(raw) == 4 and 1900 <= value <= 2100
        if value >= 32 and not is_year and value not in said:
            return False
    return True


def extract_memories(router: ModelRouter, store: MemoryStore, settings: Settings, user_text: str,
                     assistant_text: str = "", known: list[str] | None = None, turn: str = "") -> list[dict[str, Any]]:
    """Run extraction and apply it. Returns [{"action": created|merged|unchanged, "memory": {...}}]."""
    known_block = "\n".join(f"- {k}" for k in (known or [])) or "(nothing yet)"
    req = ChatRequest(messages=[
        {"role": "system", "content": EXTRACT_PROMPT},
        {"role": "user", "content": f"KNOWN:\n{known_block}\n\nUSER: {user_text}\n\n"
                                    f"ASSISTANT (context): {assistant_text[:400]}"}],
        task="extract_memory", json_mode=True, max_tokens=600, temperature=0.0, turn=turn)
    try:
        result, _ = router.chat("fast", req)
        data = parse_json(result.content)
    except (AllModelsFailed, ValueError):
        return []
    items = data.get("memories", []) if isinstance(data, dict) else []
    applied = []
    existing = store.list()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind, content = str(item.get("type", "")).lower(), str(item.get("content", "")).strip()
        try:
            confidence = float(item.get("confidence", 0.7))
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            continue
        if kind not in MEMORY_TYPES or not content or len(content) > 300 or confidence < settings.memory.min_confidence:
            continue
        if not grounded(content, user_text):
            log_event(log, "memory_dropped_ungrounded", type=kind, content=content[:120])
            continue
        twin = next((m for m in existing if m["type"] != kind
                     and similarity(m["content"], content) >= MERGE_SIMILARITY), None)
        if twin is not None:  # already known under another type
            continue
        memory, action = store.upsert(kind, content, key=str(item.get("key") or "") or None,
                                      confidence=min(confidence, 1.0), importance=min(max(importance, 0.0), 1.0),
                                      source="chat")
        applied.append({"action": action, "memory": memory})
    return applied
