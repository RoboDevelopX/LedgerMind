"""Background jobs (APScheduler): proactive checks every `proactive.interval_minutes`, memory consolidation daily."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from ..app import AppState
from ..logs import get_logger, log_event
from ..memory.consolidate import consolidate
from .checks import run_checks

log = get_logger(__name__)

StatesProvider = Callable[[], Iterable[AppState]]


def run_all_checks(states: StatesProvider) -> None:
    for st in list(states()):
        run_checks(st.ctx)


def consolidate_all(states: StatesProvider) -> None:
    for st in list(states()):
        report = consolidate(st.memory, st.settings, st.router)
        log_event(log, "memory_consolidated", merged=report["merged"], pruned=len(report["pruned"]))


def start_scheduler(states: StatesProvider, interval_minutes: int) -> BackgroundScheduler:
    """Start the jobs. The first check runs a few seconds after start-up so a fresh demo gets its cards."""
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(run_all_checks, "interval", args=[states], minutes=interval_minutes, id="proactive_checks",
                      next_run_time=datetime.now() + timedelta(seconds=5), max_instances=1, coalesce=True)
    scheduler.add_job(consolidate_all, "interval", args=[states], hours=24, id="memory_consolidation",
                      max_instances=1, coalesce=True)
    scheduler.start()
    log_event(log, "scheduler_started", interval_minutes=interval_minutes)
    return scheduler
