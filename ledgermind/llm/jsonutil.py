"""Helpers for model output: strip Nemotron reasoning traces and parse (or repair) JSON replies."""
from __future__ import annotations

import json
import re
from typing import Any

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def strip_reasoning(text: str | None) -> str:
    """Remove `<think>` blocks. An unclosed `<think>` means the reply was cut off mid-thought: no answer yet."""
    text = _THINK_RE.sub("", text or "")
    if "</think>" in text:  # opening tag was part of the prompt template
        text = text.split("</think>", 1)[1]
    if text.lstrip().lower().startswith("<think>"):
        return ""
    return text.strip()


class ThinkFilter:
    """Streaming counterpart of `strip_reasoning`: feed text deltas, get back only answer text."""

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False
        self._started = False

    def feed(self, delta: str) -> str:
        """Consume a delta and return the part of it that is safe to show."""
        self._buf += delta
        out = []
        while self._buf:
            if self._inside:
                end = self._buf.lower().find("</think>")
                if end == -1:
                    self._buf = self._buf[-8:]
                    return "".join(out)
                self._buf = self._buf[end + len("</think>"):]
                self._inside = False
                continue
            start = self._buf.lower().find("<think>")
            if start == -1:
                # Hold back a possible partial "<think" at the end of the buffer.
                keep = next((i for i in range(1, 8) if "<think>".startswith(self._buf[-i:].lower())), 0)
                emit = self._buf[: len(self._buf) - keep] if keep else self._buf
                self._buf = self._buf[len(emit):]
                if not self._started:
                    emit = emit.lstrip()
                self._started = self._started or bool(emit)
                out.append(emit)
                return "".join(out)
            out.append(self._buf[:start])
            self._buf = self._buf[start + len("<think>"):]
            self._inside = True
        return "".join(out)

    def flush(self) -> str:
        """Return any held-back text at end of stream."""
        rest, self._buf = ("" if self._inside else self._buf), ""
        return rest


def _candidates(text: str) -> list[str]:
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    found = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            found.append(text[start : end + 1])
    found.sort(key=lambda s: text.find(s))
    return found


def parse_json(text: str | None) -> Any:
    """Pull the first JSON object or array out of a model reply, repairing trailing commas and smart quotes."""
    text = strip_reasoning(text)
    for cand in _candidates(text):
        for attempt in (cand, _repair(cand)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON found in model output: {text[:200]!r}")


def _repair(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = _TRAILING_COMMA_RE.sub(r"\1", s)
    s = re.sub(r"\bTrue\b", "true", re.sub(r"\bFalse\b", "false", re.sub(r"\bNone\b", "null", s)))
    return s
