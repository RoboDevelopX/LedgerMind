"""Explicit memory control in natural language: remember, forget, recall."""
from __future__ import annotations

from typing import Any

from ..memory.retrieve import retrieve
from ..memory.store import MEMORY_TYPES, MemoryStore, tokens
from . import SkillContext, skill


@skill("remember",
       "Save something the user explicitly asks you to remember (a fact, preference, goal, habit or decision).",
       {"content": {"type": "string", "description": "one short third-person sentence"},
        "type": {"type": "string", "enum": list(MEMORY_TYPES)},
        "importance": {"type": "number", "description": "0-1, default 0.7"}},
       ("content",), mutates=True)
def remember(ctx: SkillContext, content: str, type: str = "preference", importance: float = 0.7) -> dict[str, Any]:
    """User-sourced memories get full confidence and are never pruned by consolidation."""
    memory, action = MemoryStore(ctx.db).upsert(type, content, confidence=1.0,
                                                importance=min(max(importance, 0.0), 1.0), source="user")
    return {"action": action, "memory": {"id": memory["id"], "type": memory["type"], "content": memory["content"]}}


@skill("forget",
       "Delete memories the user asks you to forget. Matches on the words in `about`; deletes every memory that "
       "contains all of them, or the single best full-text match.",
       {"about": {"type": "string", "description": "what to forget, e.g. 'my salary' or 'Japan trip'"},
        "memory_id": {"type": "integer", "description": "exact memory id, if known"}},
       mutates=True)
def forget(ctx: SkillContext, about: str = "", memory_id: int | None = None) -> dict[str, Any]:
    """Deletion is immediate and permanent; the memory panel updates on the next refresh."""
    store = MemoryStore(ctx.db)
    if memory_id is not None:
        m = store.get(memory_id)
        return {"forgotten": [m["content"]]} if m and store.delete(memory_id) else {"error": "no such memory"}
    words = tokens(about) - {"my", "me", "about", "forget", "remember"}
    if not words:
        return {"error": "say what to forget, e.g. 'forget my salary'"}
    matches = [m for m in store.list() if words <= tokens(m["content"] + " " + m["key"])]
    if not matches:
        hits = store.search(about, limit=1)
        matches = hits[:1]
    if not matches:
        return {"forgotten": [], "message": f"Nothing in memory matched {about!r}."}
    for m in matches:
        store.delete(m["id"])
    return {"forgotten": [m["content"] for m in matches]}


@skill("recall_memories",
       "List what Ledgermind remembers about the user, optionally filtered by topic or memory type.",
       {"query": {"type": "string", "description": "optional topic"},
        "type": {"type": "string", "enum": list(MEMORY_TYPES)}})
def recall_memories(ctx: SkillContext, query: str = "", type: str | None = None) -> dict[str, Any]:
    store = MemoryStore(ctx.db)
    rows = retrieve(store, query, k=15, touch=False) if query else store.list(type)
    if type:
        rows = [m for m in rows if m["type"] == type]
    return {"count": len(rows), "memories": [{"type": m["type"], "content": m["content"],
                                              "source": m["source"], "updated": m["updated_at"][:10]}
                                             for m in rows[:25]]}
