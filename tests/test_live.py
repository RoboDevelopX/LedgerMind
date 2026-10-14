"""Optional live checks against Nebius Token Factory. Skipped unless LEDGERMIND_LIVE_TESTS=1 and a key is set:

    LEDGERMIND_LIVE_TESTS=1 pytest tests/test_live.py -q
"""
import os

import pytest

from ledgermind.agent.loop import Agent
from ledgermind.app import AppState
from ledgermind.config import load_dotenv, load_settings, DEFAULT_CONFIG
from ledgermind.llm import TokenFactoryClient

load_dotenv(DEFAULT_CONFIG.parent / ".env")
LIVE = os.environ.get("LEDGERMIND_LIVE_TESTS") == "1" and bool(os.environ.get("NEBIUS_API_KEY"))
pytestmark = pytest.mark.skipif(not LIVE, reason="set LEDGERMIND_LIVE_TESTS=1 and NEBIUS_API_KEY for live tests")


@pytest.fixture
def live_settings():
    env = {k: v for k, v in os.environ.items() if k != "LEDGERMIND_MOCK"}
    return load_settings(env=env)


def test_configured_models_exist(live_settings):
    available = set(TokenFactoryClient(live_settings).list_models())
    missing = [m.id for m in live_settings.models.values() if m.id not in available]
    assert not missing, f"not on Token Factory for this key: {missing}"


def test_agent_answers_with_ledger_figures(tmp_path, live_settings):
    state = AppState(settings=live_settings, db_path=str(tmp_path / "live.db"))
    state.load_demo()
    out = Agent(state).handle("How much did I spend on dining last month?")
    assert "spending_summary" in [t["name"] for t in out["tools"]]
    assert out["tool_results"]["spending_summary"]["spent"].replace("₹", "") in out["reply"].replace("₹", "")
    assert not out["calls"][0]["mock"]
