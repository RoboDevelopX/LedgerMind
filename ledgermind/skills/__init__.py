"""Skills: reusable, declarative finance tools the agent can call.

Each skill is a Python function, a JSON schema for its arguments, and a short description (the function's
docstring holds the longer docs). Skills compute every number deterministically from the local ledger; models
only decide which skill to call and explain the result.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from ..config import Settings
from ..db import Database
from ..logs import get_logger, log_event

log = get_logger("skills")


@dataclass
class SkillContext:
    """What a skill may use. `router` is None when no model is available (pure deterministic mode)."""

    db: Database
    settings: Settings
    router: Any | None = None


@dataclass
class Skill:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., dict[str, Any]]
    narrate_with: str | None = None   # "deep" = the final answer for this tool is written by Nemotron Ultra
    mutates: bool = False             # changes the ledger or memory
    agent_visible: bool = True        # offered to the model as a tool
    docs: str = ""

    def tool(self) -> dict[str, Any]:
        """OpenAI-format tool definition."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


REGISTRY: dict[str, Skill] = {}


def skill(name: str, description: str, properties: dict[str, Any] | None = None, required: tuple[str, ...] = (),
          *, narrate_with: str | None = None, mutates: bool = False, agent_visible: bool = True):
    """Register a function as a skill. `properties` is a JSON-schema properties map."""
    def deco(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        params = {"type": "object", "properties": properties or {}, "required": list(required)}
        REGISTRY[name] = Skill(name, description, params, fn, narrate_with, mutates, agent_visible,
                               inspect.getdoc(fn) or "")
        return fn
    return deco


def tools(names: list[str] | None = None) -> list[dict[str, Any]]:
    """Tool definitions for the agent."""
    return [s.tool() for n, s in REGISTRY.items() if s.agent_visible and (names is None or n in names)]


class SkillArgumentError(ValueError):
    """Arguments from the model did not match the skill's schema."""


def _coerce(name: str, value: Any, spec: dict[str, Any]) -> Any:
    kind = spec.get("type")
    try:
        if kind == "number":
            value = float(str(value).replace(",", "").replace("₹", "").replace("$", "").rstrip("%"))
        elif kind == "integer":
            value = int(float(str(value)))
        elif kind == "boolean":
            value = value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
        elif kind == "string":
            value = str(value).strip()
        elif kind == "array" and not isinstance(value, list):
            value = [value]
    except (TypeError, ValueError) as exc:
        raise SkillArgumentError(f"{name} should be a {kind}, got {value!r}") from exc
    if "enum" in spec and value not in spec["enum"]:
        # Models often change the case of enum values ("dining" for "Dining"); accept that, nothing looser.
        match = [e for e in spec["enum"] if str(e).lower() == str(value).lower()]
        if not match:
            raise SkillArgumentError(f"{name} must be one of {spec['enum']}, got {value!r}")
        value = match[0]
    return value


def validate(s: Skill, args: dict[str, Any] | None) -> dict[str, Any]:
    """Drop unknown args, coerce types, enforce required ones."""
    props = s.parameters["properties"]
    clean = {k: _coerce(k, v, props[k]) for k, v in (args or {}).items() if k in props and v not in (None, "")}
    missing = [r for r in s.parameters["required"] if r not in clean]
    if missing:
        raise SkillArgumentError(f"missing required argument(s): {', '.join(missing)}")
    return clean


def run_skill(ctx: SkillContext, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a skill by name. Bad arguments or failures come back as {"error": ...}, never as exceptions."""
    s = REGISTRY.get(name)
    if s is None:
        return {"error": f"unknown tool {name!r}. Available: {', '.join(sorted(REGISTRY))}"}
    try:
        result = s.fn(ctx, **validate(s, args))
    except SkillArgumentError as exc:
        return {"error": f"invalid arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - a broken skill must not crash the conversation
        log_event(log, "skill_failed", skill=name, error=repr(exc))
        return {"error": f"{name} failed: {exc}"}
    log_event(log, "skill_ran", skill=name, args=args or {})
    return result


def _register_all() -> None:
    from . import (anomalies, budgets, categorize, goals, importer, memory_skills, review,  # noqa: F401
                   subscriptions, summary, whatif)


_register_all()
