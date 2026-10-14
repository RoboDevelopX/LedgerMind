"""The agent loop.

One user turn:
1. Retrieve the top-k memories (FTS5 + importance + recency) and build the system prompt.
2. Tool-calling loop on Nemotron Super (max `agent.max_iterations` rounds). Tool arguments that are not valid JSON
   are repaired, or the model is asked once to resend them. Tool results are minimised and redacted before they
   go back to the model. Once a deep-reasoning tool has run (what-if, goal planning, anomaly explanation), the rest
   of the turn moves to Nemotron Ultra.
3. The final answer streams token by token. The number guard checks every figure against the tool results and
   memory, and regenerates the answer once if something doesn't match.
4. Nemotron Nano extracts new memories from the exchange.

Every step is emitted as an `Event`, which the server streams to the UI as SSE.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..app import AppState
from ..db import dumps, now_iso
from ..ledger import date_range
from ..llm import AllModelsFailed, CallRecord, ChatRequest, ChatResult, StreamEvent, ToolCall
from ..llm.jsonutil import parse_json
from ..memory.extract import extract_memories
from ..memory.retrieve import retrieve
from ..money import render_for_model
from ..periods import today
from ..privacy.minimise import minimise
from ..skills import REGISTRY, run_skill, tools
from . import numguard
from .prompts import FORCE_ANSWER, GUARD_RETRY, SYSTEM_PROMPT


@dataclass
class Event:
    """One step of a turn. `type`: turn, memories, token, reset, tool_call, tool_result, replace, answer,
    memory_saved, done, error."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def sse(self) -> str:
        return f"event: {self.type}\ndata: {json.dumps(self.data, default=str, ensure_ascii=False)}\n\n"


def _call_badge(rec: CallRecord) -> dict[str, Any]:
    return {"role": rec.role, "task": rec.task, "model": rec.model, "label": rec.label, "latency_ms": rec.latency_ms,
            "tokens": rec.prompt_tokens + rec.completion_tokens, "cost_usd": rec.cost_usd,
            "fallback_from": rec.fallback_from, "mock": rec.mock}


class Agent:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.settings = state.settings

    # ------------------------------------------------------------------ context
    def _system(self, memories: list[dict[str, Any]]) -> str:
        lo, hi = date_range(self.state.db)
        coverage = f"{lo} to {hi}" if lo else "empty (ask the user to import a bank CSV or load the demo ledger)"
        mem = "\n".join(f"- [{m['type']}] {m['content']}" for m in memories) or "(nothing yet)"
        return SYSTEM_PROMPT.format(today=today().isoformat(), currency=self.settings.currency, coverage=coverage,
                                    memories=mem)

    def _history(self) -> list[dict[str, Any]]:
        n = self.settings.agent.history_turns
        rows = self.state.db.all("SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", [n])
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # ------------------------------------------------------------------ tools
    def _run_tool(self, call: ToolCall, turn: str, retried: set[str]) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Returns (full result, minimised view for the model, whether anything was withheld)."""
        try:
            args = parse_json(call.arguments) if call.arguments.strip() not in ("", "{}") else {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError:
            if call.name in retried:
                result = {"error": "arguments were still not valid JSON; answer with what you have"}
            else:
                retried.add(call.name)
                result = {"error": "arguments were not valid JSON. Call the tool again with a JSON object."}
            return result, result, False
        result = run_skill(self.state.ctx, call.name, args)
        view, withheld = minimise(result, strict=self.settings.privacy.strict_mode,
                                  max_rows=self.settings.privacy.max_rows_per_tool, currency=self.settings.currency)
        self.state.privacy.note_withheld(turn, [f"{call.name}: {w}" for w in withheld])
        return result, view, bool(withheld)

    # ------------------------------------------------------------------ model
    def _stream(self, role: str, req: ChatRequest, calls: list[CallRecord]) -> Iterator[Event | ChatResult]:
        for item in self.state.router.stream(role, req):
            if isinstance(item, CallRecord):
                calls.append(item)
            elif isinstance(item, StreamEvent) and item.kind == "text":
                if item.text:
                    yield Event("token", {"text": item.text})
            elif isinstance(item, StreamEvent) and item.result is not None:
                yield item.result

    # ------------------------------------------------------------------ turn
    def run(self, user_text: str) -> Iterator[Event]:
        """Run one turn, yielding events as they happen."""
        turn = uuid.uuid4().hex[:12]
        started = time.monotonic()
        calls: list[CallRecord] = []
        cfg = self.settings.agent
        memories = retrieve(self.state.memory, user_text, k=cfg.memory_top_k)
        yield Event("turn", {"turn": turn})
        yield Event("memories", {"used": [{"id": m["id"], "type": m["type"], "content": m["content"]}
                                          for m in memories]})
        history = self._history()
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system(memories)}, *history,
                                          {"role": "user", "content": user_text}]
        self.state.db.execute("INSERT INTO messages(role, content, meta, created_at) VALUES (?,?,?,?)",
                              ["user", user_text, dumps({"turn": turn}), now_iso()])
        sources: list[Any] = [user_text, messages[0]["content"], *[h["content"] for h in history]]
        steps: list[dict[str, Any]] = []
        retried: set[str] = set()
        role, answer = "agent", ""
        try:
            for iteration in range(cfg.max_iterations + 1):
                last_round = iteration == cfg.max_iterations
                if last_round:
                    messages.append({"role": "user", "content": FORCE_ANSWER})
                req = ChatRequest(messages=messages, task="agent" if role == "agent" else "deep_answer",
                                  tools=None if last_round else tools(), max_tokens=cfg.max_tokens,
                                  temperature=cfg.temperature, turn=turn)
                result: ChatResult | None = None
                for item in self._stream(role, req, calls):
                    if isinstance(item, Event):
                        yield item
                    else:
                        result = item
                assert result is not None
                if not result.tool_calls:
                    answer = result.content
                    break
                if result.content:
                    yield Event("reset", {})
                messages.append({"role": "assistant", "content": result.content or None, "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in result.tool_calls]})
                for call in result.tool_calls:
                    yield Event("tool_call", {"id": call.id, "name": call.name, "arguments": call.arguments,
                                              "model": calls[-1].label if calls else ""})
                    t0 = time.monotonic()
                    full, view, withheld = self._run_tool(call, turn, retried)
                    ms = int((time.monotonic() - t0) * 1000)
                    steps.append({"name": call.name, "arguments": call.arguments, "ms": ms,
                                  "ok": "error" not in full})
                    sources.append(view)
                    # `result` is exactly what the model saw; `local` is the full result, shown only in the UI.
                    local = render_for_model(full, self.settings.currency) if withheld else None
                    yield Event("tool_result", {"id": call.id, "name": call.name, "ms": ms, "result": view,
                                                "local": local, "ok": "error" not in full})
                    messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                     "content": json.dumps(view, ensure_ascii=False, default=str)})
                    skill = REGISTRY.get(call.name)
                    if skill is not None and skill.narrate_with == "deep" and "error" not in full:
                        role = "deep"
            answer = answer or "I couldn't put an answer together. Please try rephrasing."
            guard = numguard.check(answer, *sources)
            if not guard.ok:
                messages += [{"role": "assistant", "content": answer},
                             {"role": "user", "content": GUARD_RETRY.format(numbers=", ".join(guard.unsupported))}]
                fixed, rec = self.state.router.chat(role, ChatRequest(
                    messages=messages, task="guard_retry", max_tokens=cfg.max_tokens, temperature=0.0, turn=turn))
                calls.append(rec)
                second = numguard.check(fixed.content, *sources)
                answer = fixed.content if fixed.content and second.ok else answer + (
                    "\n\n_Note: I couldn't verify these figures against your ledger: "
                    + ", ".join(second.unsupported or guard.unsupported) + "._")
                guard = second
                yield Event("replace", {"text": answer})
        except AllModelsFailed as exc:
            answer = str(exc)
            guard = numguard.GuardResult(ok=True)
            yield Event("error", {"message": answer})
        yield Event("answer", {"text": answer, "verified": guard.ok, "checked_numbers": guard.checked})
        badges = [_call_badge(c) for c in calls]
        self.state.db.execute(
            "INSERT INTO messages(role, content, meta, created_at) VALUES (?,?,?,?)",
            ["assistant", answer, dumps({"turn": turn, "tools": steps, "calls": badges, "verified": guard.ok}),
             now_iso()])
        # Explicit memory tools already stored what the user said; extracting again would duplicate it.
        explicit = {"remember", "forget", "set_goal", "set_budget", "correct_category"} & {s["name"] for s in steps}
        learned = [] if explicit else extract_memories(self.state.router, self.state.memory, self.settings,
                                                       user_text, answer, known=[m["content"] for m in memories],
                                                       turn=turn)
        if learned:
            yield Event("memory_saved", {"memories": [{"action": x["action"], "id": x["memory"]["id"],
                                                       "type": x["memory"]["type"],
                                                       "content": x["memory"]["content"]} for x in learned]})
        self.state.privacy.end_turn(turn)
        final_calls = [c for c in calls if c.task in ("agent", "deep_answer", "guard_retry")]
        yield Event("done", {"turn": turn, "total_ms": int((time.monotonic() - started) * 1000), "calls": badges,
                             "answer_model": final_calls[-1].label if final_calls else None,
                             "answer_latency_ms": final_calls[-1].latency_ms if final_calls else None,
                             "tools": steps, "verified": guard.ok})

    def handle(self, user_text: str) -> dict[str, Any]:
        """Run a turn to completion and collect the result (tests, eval, non-streaming API)."""
        out: dict[str, Any] = {"tools": [], "tool_results": {}, "tool_results_local": {}, "memories_used": [],
                               "learned": []}
        for ev in self.run(user_text):
            if ev.type == "tool_call":
                out["tools"].append({"name": ev.data["name"], "arguments": ev.data["arguments"]})
            elif ev.type == "tool_result":
                out["tool_results"][ev.data["name"]] = ev.data["result"]
                out["tool_results_local"][ev.data["name"]] = ev.data["local"] or ev.data["result"]
            elif ev.type == "memories":
                out["memories_used"] = ev.data["used"]
            elif ev.type == "memory_saved":
                out["learned"] = ev.data["memories"]
            elif ev.type == "answer":
                out["reply"] = ev.data["text"]
                out["verified"] = ev.data["verified"]
            elif ev.type == "done":
                out.update({"calls": ev.data["calls"], "answer_model": ev.data["answer_model"],
                            "total_ms": ev.data["total_ms"], "turn": ev.data["turn"]})
            elif ev.type == "error":
                out["error"] = ev.data["message"]
        return out
