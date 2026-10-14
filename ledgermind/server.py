"""FastAPI app: streaming chat, CSV import, dashboard, memory panel, Privacy Inspector and proactive notices.

    uvicorn ledgermind.server:app --reload
"""
from __future__ import annotations

import re
import secrets
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agent.loop import Agent
from .app import AppState
from .config import get_settings
from .ledger import count, date_range, frame, transactions
from .logs import setup_logging
from .memory.consolidate import consolidate
from .memory.store import MEMORY_TYPES
from .money import money
from .proactive.checks import dismiss, list_notices, run_checks
from .proactive.scheduler import start_scheduler
from .periods import add_months, month_end, resolve_period, today
from .skills.budgets import budget_rows
from .skills.goals import goal_progress
from .skills.importer import CSVImportError, import_text
from .skills.review import review_markdown
from .skills.subscriptions import detect_subscriptions
from .skills.summary import monthly_baseline, summarize, totals

STATIC = Path(__file__).parent / "static"
COOKIE = "ledgermind_sid"
MAX_UPLOAD = 5_000_000

setup_logging()
settings = get_settings()
_states: dict[str, AppState] = {}
_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the proactive scheduler (unless disabled) and stop it on shutdown."""
    scheduler = None
    if settings.proactive.enabled:
        if not settings.multiuser:
            local_state()
        scheduler = start_scheduler(lambda: list(_states.values()), settings.proactive.interval_minutes)
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Ledgermind", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def local_state() -> AppState:
    """The single-user ledger at `db_path`."""
    with _lock:
        if "local" not in _states:
            _states["local"] = AppState(settings=settings.model_copy(deep=True), db_path=settings.db_path)
        return _states["local"]


def state_for(request: Request, response: Response | None = None) -> AppState:
    """The caller's ledger. Multi-user mode gives each browser its own SQLite file."""
    if not settings.multiuser:
        return local_state()
    sid = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", sid):
        sid = secrets.token_urlsafe(24)
        if response is not None:
            response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    with _lock:
        if sid not in _states:
            # Each ledger gets its own copy of the settings so toggles like strict mode stay per user.
            _states[sid] = AppState(settings=settings.model_copy(deep=True),
                                    db_path=str(Path(settings.data_dir) / f"{sid}.db"))
        return _states[sid]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class MemoryIn(BaseModel):
    content: str | None = Field(default=None, max_length=300)
    type: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    pinned: bool | None = None


class ToggleIn(BaseModel):
    enabled: bool


# ------------------------------------------------------------------ pages

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/status")
def status(request: Request, response: Response) -> dict[str, Any]:
    st = state_for(request, response)
    lo, hi = date_range(st.db)
    return {
        "version": __version__, "mock": st.settings.mock, "provider": st.settings.provider.name,
        "base_url": st.settings.provider.base_url, "currency": st.settings.currency,
        "strict_mode": st.settings.privacy.strict_mode,
        "roles": {role: [{"alias": a, "id": st.settings.models[a].id, "label": st.settings.models[a].label}
                         for a in aliases] for role, aliases in st.settings.roles.items()},
        "ledger": {"transactions": count(st.db), "from": lo, "to": hi}, "memories": st.memory.count(),
        "multiuser": st.settings.multiuser,
    }


# ------------------------------------------------------------------ chat

@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, request: Request, response: Response) -> StreamingResponse:
    st = state_for(request, response)
    agent = Agent(st)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse((ev.sse() for ev in agent.run(body.message.strip())), media_type="text/event-stream",
                             headers=headers)


@app.post("/api/chat")
def chat(body: ChatIn, request: Request, response: Response) -> dict[str, Any]:
    return Agent(state_for(request, response)).handle(body.message.strip())


@app.get("/api/history")
def history(request: Request, response: Response, limit: int = 40) -> list[dict[str, Any]]:
    rows = state_for(request, response).db.all("SELECT * FROM messages ORDER BY id DESC LIMIT ?", [limit])
    return list(reversed(rows))


# ------------------------------------------------------------------ data in / out

@app.post("/api/import")
async def import_csv(request: Request, response: Response, file: UploadFile = File(...),
                     account: str = Form("default")) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "That file is larger than 5 MB.")
    st = state_for(request, response)
    try:
        result = import_text(st.ctx, raw.decode("utf-8-sig", errors="replace"), account=account)
    except CSVImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    st.privacy.refresh()
    result["proactive"] = run_checks(st.ctx)
    return result


@app.post("/api/demo")
def load_demo(request: Request, response: Response) -> dict[str, Any]:
    st = state_for(request, response)
    result = st.load_demo(reset=True)
    result["proactive"] = run_checks(st.ctx)
    return result


@app.get("/api/export")
def export(request: Request, response: Response) -> JSONResponse:
    data = state_for(request, response).db.export()
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=ledgermind-export.json"})


@app.post("/api/wipe")
def wipe(request: Request, response: Response) -> dict[str, bool]:
    state_for(request, response).db.wipe()
    return {"ok": True}


# ------------------------------------------------------------------ dashboard

@app.get("/api/dashboard")
def dashboard(request: Request, response: Response) -> dict[str, Any]:
    st = state_for(request, response)
    if count(st.db) == 0:
        return {"empty": True}
    ctx = st.ctx
    t = today()
    start = add_months(t, -11)
    df = frame(st.db, start, t)
    trend = []
    for month, g in df.groupby("month"):
        tt = totals(g)
        trend.append({"month": month, "income": money(tt["income"]), "spent": money(tt["spent"]),
                      "saved": money(tt["saved"])})
    subs = detect_subscriptions(ctx)
    return {
        "empty": False, "currency": st.settings.currency,
        "this_month": summarize(ctx, resolve_period("this_month")),
        "last_month": summarize(ctx, resolve_period("last_month")),
        "trend": trend, "budgets": budget_rows(ctx), "goals": goal_progress(ctx),
        "subscriptions": {"count": len(subs["subscriptions"]), "monthly_total": subs["subscriptions_monthly_total"],
                          "yearly_total": subs["subscriptions_yearly_total"], "price_hikes": subs["price_hikes"],
                          "possibly_forgotten": subs["possibly_forgotten"],
                          "items": [{k: r[k] for k in ("merchant", "cadence", "amount", "monthly_equivalent",
                                                       "next_expected", "flags", "price_change")}
                                    for r in subs["subscriptions"]]},
        "baseline": monthly_baseline(ctx),
        "recent": [{k: r[k] for k in ("date", "merchant", "amount", "category")}
                   for r in transactions(st.db, limit=12)],
        "as_of": t.isoformat(), "month_end": month_end(t).isoformat(),
    }


# ------------------------------------------------------------------ memory panel

@app.get("/api/memories")
def memories(request: Request, response: Response) -> dict[str, Any]:
    st = state_for(request, response)
    return {"types": list(MEMORY_TYPES), "memories": st.memory.list()}


@app.post("/api/memories")
def add_memory(body: MemoryIn, request: Request, response: Response) -> dict[str, Any]:
    if not body.content or (body.type and body.type not in MEMORY_TYPES):
        raise HTTPException(400, "content and a valid type are required")
    mem, action = state_for(request, response).memory.upsert(body.type or "preference", body.content,
                                                              importance=body.importance or 0.7, confidence=1.0,
                                                              source="user")
    return {"action": action, "memory": mem}


@app.patch("/api/memories/{memory_id}")
def edit_memory(memory_id: int, body: MemoryIn, request: Request, response: Response) -> dict[str, Any]:
    try:
        mem = state_for(request, response).memory.update(memory_id, content=body.content, type=body.type,
                                                         importance=body.importance, pinned=body.pinned)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if mem is None:
        raise HTTPException(404, "no such memory")
    return mem


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int, request: Request, response: Response) -> dict[str, bool]:
    return {"ok": state_for(request, response).memory.delete(memory_id)}


@app.post("/api/memories/consolidate")
def consolidate_memories(request: Request, response: Response) -> dict[str, Any]:
    st = state_for(request, response)
    return consolidate(st.memory, st.settings, st.router)


# ------------------------------------------------------------------ privacy

@app.get("/api/privacy/last")
def privacy_last(request: Request, response: Response) -> dict[str, Any]:
    return state_for(request, response).privacy.last_turn()


@app.post("/api/privacy/strict")
def privacy_strict(body: ToggleIn, request: Request, response: Response) -> dict[str, bool]:
    st = state_for(request, response)
    st.settings.privacy.strict_mode = body.enabled
    return {"strict_mode": st.settings.privacy.strict_mode}


@app.get("/api/calls")
def calls(request: Request, response: Response, limit: int = 50) -> list[dict[str, Any]]:
    return state_for(request, response).db.all("SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", [limit])


# ------------------------------------------------------------------ proactive

@app.get("/api/notices")
def notices(request: Request, response: Response) -> dict[str, Any]:
    return {"notices": list_notices(state_for(request, response).ctx)}


@app.post("/api/notices/{notice_id}/dismiss")
def dismiss_notice(notice_id: int, request: Request, response: Response) -> dict[str, bool]:
    return {"ok": dismiss(state_for(request, response).ctx, notice_id)}


@app.post("/api/proactive/run")
def run_proactive(request: Request, response: Response) -> dict[str, Any]:
    return run_checks(state_for(request, response).ctx)


# ------------------------------------------------------------------ reviews

@app.get("/api/review/{month}.md")
def review_md(month: str, request: Request, response: Response) -> PlainTextResponse:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month must be YYYY-MM")
    text = review_markdown(state_for(request, response).ctx, month)
    if text is None:
        raise HTTPException(404, "No review for that month yet. Ask Ledgermind for a monthly review first.")
    return PlainTextResponse(text, media_type="text/markdown",
                             headers={"Content-Disposition": f"attachment; filename=ledgermind-review-{month}.md"})


@app.exception_handler(pd.errors.ParserError)
def _parser_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": f"Could not read that data: {exc}"}, status_code=400)
