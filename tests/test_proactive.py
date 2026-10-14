"""Proactive checks, notices and the background scheduler."""
import pytest
from fastapi.testclient import TestClient

from ledgermind import server
from ledgermind.app import AppState
from ledgermind.config import load_settings
from ledgermind.proactive.checks import dismiss, list_notices, new_subscriptions, run_checks
from ledgermind.proactive.scheduler import run_all_checks, start_scheduler
from ledgermind.skills import run_skill


@pytest.fixture
def state(tmp_path):
    s = AppState(settings=load_settings(env={"LEDGERMIND_MOCK": "1"}), db_path=str(tmp_path / "p.db"))
    s.load_demo()
    return s


def titles(state):
    return [n["title"] for n in list_notices(state.ctx)]


def test_checks_find_the_planted_events(state):
    result = run_checks(state.ctx)
    found = titles(state)
    assert result["created"] == len(found)
    for expected in ("New subscription: Audible", "Netflix raised its price", "Still using Apple Music?",
                     "Possible duplicate charge from Amazon", "Your week in money"):
        assert expected in found
    netflix = next(n for n in list_notices(state.ctx) if n["title"] == "Netflix raised its price")
    assert netflix["body"] == "From ₹649 to ₹799 (23.1% more)." and netflix["ask"]
    assert list_notices(state.ctx)[0]["severity"] == "alert"


def test_new_subscription_needs_two_matching_monthly_charges(state):
    assert [n["merchant"] for n in new_subscriptions(state.ctx)] == ["Audible"]


def test_checks_are_idempotent_and_dismiss_sticks(state):
    run_checks(state.ctx)
    assert run_checks(state.ctx)["created"] == 0
    first = list_notices(state.ctx)[0]
    assert dismiss(state.ctx, first["id"])
    run_checks(state.ctx)
    assert first["title"] not in titles(state)


def test_budget_alerts(state):
    run_skill(state.ctx, "set_budget", {"category": "Shopping", "monthly_limit": 5000})
    run_checks(state.ctx)
    assert "Shopping budget exceeded" in titles(state)


def test_recurring_memories_from_the_detector(state):
    run_checks(state.ctx)
    recurring = {m["content"] for m in state.memory.list("recurring")}
    assert "Pays Netflix ₹799 monthly (subscription)." in recurring
    assert all(m["source"] == "detector" for m in state.memory.list("recurring"))
    assert run_checks(state.ctx)["recurring_memories"] == 0


def test_empty_ledger_has_nothing_to_say(tmp_path):
    s = AppState(settings=load_settings(env={"LEDGERMIND_MOCK": "1"}), db_path=str(tmp_path / "e.db"))
    assert run_checks(s.ctx) == {"created": 0, "notices": [], "recurring_memories": 0}


def test_scheduler_runs_checks_for_every_ledger(state):
    run_all_checks(lambda: [state])
    assert titles(state)
    scheduler = start_scheduler(lambda: [state], interval_minutes=60)
    try:
        assert {j.id for j in scheduler.get_jobs()} == {"proactive_checks", "memory_consolidation"}
    finally:
        scheduler.shutdown(wait=False)


def test_notice_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "settings", load_settings(env={"LEDGERMIND_MOCK": "1", "LEDGERMIND_PROACTIVE": "0",
                                                               "LEDGERMIND_DB": str(tmp_path / "n.db")}))
    monkeypatch.setattr(server, "_states", {})
    client = TestClient(server.app)
    loaded = client.post("/api/demo").json()
    assert loaded["proactive"]["created"] >= 5
    notices = client.get("/api/notices").json()["notices"]
    assert client.post(f"/api/notices/{notices[0]['id']}/dismiss").json() == {"ok": True}
    assert len(client.get("/api/notices").json()["notices"]) == len(notices) - 1
    assert client.post("/api/proactive/run").json()["created"] == 0
