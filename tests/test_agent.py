"""Goals, budgets, what-if maths, the number guard, the agent loop and monthly reviews (models mocked)."""
import json
import math

import pytest

from ledgermind.agent import numguard
from ledgermind.agent.loop import Agent
from ledgermind.app import AppState
from ledgermind.config import load_settings
from ledgermind.ledger import frame
from ledgermind.llm import ChatRequest, ChatResult, ToolCall
from ledgermind.llm.mock import mock_brain
from ledgermind.merchants import NON_SPENDING
from ledgermind.periods import add_months, month_end, today
from ledgermind.skills import run_skill
from ledgermind.skills.review import review_markdown


@pytest.fixture
def state(tmp_path):
    s = AppState(settings=load_settings(env={"LEDGERMIND_MOCK": "1"}), db_path=str(tmp_path / "a.db"))
    s.load_demo()
    return s


def baseline_from_pandas(state):
    """The last 3 full months, computed independently of the skills."""
    df = frame(state.db, add_months(today(), -3), month_end(add_months(today(), -1)))
    n = df.month.nunique()
    spend = df[~df.category.isin(NON_SPENDING)]
    by_cat = -spend.groupby("category").amount.sum() / n
    income = df[(df.amount > 0) & (df.category == "Income")].amount.sum() / n
    return by_cat, income, income - by_cat.sum()


def agent_with(state, brain):
    """An agent whose models answer with `brain` for agent/deep tasks and the stock mock otherwise."""
    state.router.client.brain = lambda req: brain(req) if req.task in ("agent", "deep_answer", "guard_retry") \
        else mock_brain(req)
    return Agent(state)


def tool_messages(req: ChatRequest) -> list[dict]:
    return [m for m in req.messages if m["role"] == "tool"]


# ---------------------------------------------------------------- deterministic maths

def test_what_if_matches_pandas(state):
    by_cat, _, surplus = baseline_from_pandas(state)
    run_skill(state.ctx, "set_goal", {"name": "Japan trip", "target_amount": 300000, "saved_so_far": 50000})
    out = run_skill(state.ctx, "what_if_simulator", {"category": "dining", "change_pct": -30, "months": 12})
    row = out["changes"][0]
    assert row["category"] == "Dining"
    assert float(row["current_monthly"]) == pytest.approx(by_cat["Dining"], abs=0.01)
    assert float(row["monthly_difference"]) == pytest.approx(by_cat["Dining"] * 0.3, abs=0.01)
    assert float(out["total_change_over_horizon"]) == pytest.approx(by_cat["Dining"] * 0.3 * 12, abs=0.1)
    assert float(out["surplus_before"]) == pytest.approx(surplus, abs=0.01)
    assert float(out["surplus_after"]) == pytest.approx(surplus + by_cat["Dining"] * 0.3, abs=0.01)
    goal = out["goals"][0]
    assert goal["months_to_goal_before"] == math.ceil(250000 / surplus)
    after = math.ceil(250000 / (surplus + by_cat["Dining"] * 0.3))
    assert goal["months_sooner"] == goal["months_to_goal_before"] - after


def test_what_if_income_and_bad_input(state):
    _, income, _ = baseline_from_pandas(state)
    out = run_skill(state.ctx, "what_if_simulator", {"income_change_pct": 10})
    assert float(out["monthly_saving_change"]) == pytest.approx(income * 0.1, abs=0.01)
    assert "error" in run_skill(state.ctx, "what_if_simulator", {})
    assert "error" in run_skill(state.ctx, "what_if_simulator", {"category": "Spaceships", "change_pct": -10})


def test_goal_progress_maths(state):
    _, _, surplus = baseline_from_pandas(state)
    out = run_skill(state.ctx, "set_goal", {"name": "Car", "target_amount": 600000, "deadline": "2027-10-01",
                                            "saved_so_far": 120000})
    g = out["goals"][0]
    assert g["months_left"] == 12 and float(g["remaining"]) == 480000 and g["pct_done"] == 20.0
    assert float(g["needed_per_month"]) == pytest.approx(40000)
    assert g["months_to_finish_using_full_surplus"] == math.ceil(480000 / surplus)
    assert out["fits_in_surplus"] == (40000 <= surplus)
    assert state.memory.list(type="goal")[0]["content"].startswith("Saving for Car: target ₹6,00,000")
    assert "error" in run_skill(state.ctx, "set_goal", {"name": "X", "target_amount": 5, "deadline": "soon"})


def test_budget_check_matches_pandas(state):
    run_skill(state.ctx, "set_budget", {"category": "dining", "monthly_limit": 8000})
    out = run_skill(state.ctx, "budget_check", {})
    df = frame(state.db, today().replace(day=1), today())
    spent = -df[df.category == "Dining"].amount.sum()
    row = out["budgets"][0]
    assert row["category"] == "Dining" and float(row["spent"]) == pytest.approx(spent)
    assert row["pct_used"] == round(spent / 8000 * 100, 1)
    assert float(row["projected_month_end"]) == pytest.approx(spent / 20 * 31)
    assert row["status"] == ("over" if spent > 8000 else "at_risk" if row["projected_pct"] > 100 else "on_track")
    assert "error" in run_skill(state.ctx, "set_budget", {"category": "Income", "monthly_limit": 5})


def test_budget_suggestions_when_none_set(state):
    out = run_skill(state.ctx, "budget_check", {})
    assert out["budgets"] == [] and len(out["suggestions"]) == 3


# ---------------------------------------------------------------- number guard

def test_number_guard_accepts_copied_and_rounded_figures():
    src = {"spent": "₹1,20,000", "change_pct": 23.08, "monthly": "₹26,519"}
    assert numguard.check("You spent ₹1,20,000 (up 23.08%), about ₹26,500 a month.", src).ok
    assert numguard.check("That's 1.2 lakh, or roughly 23% more, across 3 categories in 2026.", src).ok


def test_number_guard_rejects_invented_figures():
    src = {"spent": "₹1,20,000", "change_pct": 23.08}
    result = numguard.check("You spent ₹1,20,000, so cutting 30% saves ₹36,000.", src)
    assert not result.ok and result.unsupported == ["30%", "₹36,000"]
    assert not numguard.check("Spending rose 12%.", src).ok


# ---------------------------------------------------------------- agent loop

def test_agent_answers_with_tool_figures(state):
    out = Agent(state).handle("How much did I spend on dining last month?")
    assert [t["name"] for t in out["tools"]] == ["spending_summary"]
    assert json.loads(out["tools"][0]["arguments"]) == {"period": "last_month", "category": "Dining"}
    spent = out["tool_results"]["spending_summary"]["spent"]
    assert spent in out["reply"] and out["verified"]
    assert out["answer_model"] == "Nemotron 3 Super" and out["calls"][0]["latency_ms"] > 0


def test_deep_tools_hand_the_answer_to_ultra(state):
    run_skill(state.ctx, "set_goal", {"name": "Japan trip", "target_amount": 300000})
    out = Agent(state).handle("What if I cut dining by 30%?")
    assert out["tools"][0]["name"] == "what_if_simulator"
    assert out["answer_model"] == "Nemotron 3 Ultra" and out["verified"]
    agent_calls = [c for c in out["calls"] if c["task"] in ("agent", "deep_answer")]
    assert [c["label"] for c in agent_calls] == ["Nemotron 3 Super", "Nemotron 3 Ultra"]


def test_malformed_tool_arguments_are_retried_once(state):
    def brain(req):
        n = len(tool_messages(req))
        if n == 0:
            return ChatResult(content="", tool_calls=[ToolCall("c1", "spending_summary", '{"period": "last_month",,')])
        if n == 1:
            return ChatResult(content="", tool_calls=[ToolCall("c2", "spending_summary", "{'period' 'last_month'")])
        return ChatResult(content="Here is what I have.")

    out = agent_with(state, brain).handle("summary please")
    # The first call had a trailing comma that parse_json repairs; the second is unrecoverable.
    assert "error" not in out["tool_results"]["spending_summary"] or len(out["tools"]) == 2
    assert out["reply"] == "Here is what I have."
    sent = [r for _, r in state.router.client.requests if r.task == "agent"][-1]
    assert "not valid JSON" in tool_messages(sent)[-1]["content"]


def test_unknown_tool_is_reported_back_not_raised(state):
    def brain(req):
        if not tool_messages(req):
            return ChatResult(content="", tool_calls=[ToolCall("c1", "transfer_money", "{}")])
        return ChatResult(content="I can't do that, but I can summarise your spending.")

    out = agent_with(state, brain).handle("send 500 to my friend")
    assert "error" in out["tool_results"]["transfer_money"]
    assert out["reply"].startswith("I can't do that")


def test_max_iterations_forces_an_answer(state):
    def brain(req):
        if req.tools:
            return ChatResult(content="", tool_calls=[ToolCall(f"c{len(req.messages)}", "budget_check", "{}")])
        return ChatResult(content="Forced answer.")

    out = agent_with(state, brain).handle("loop forever")
    assert len(out["tools"]) == state.settings.agent.max_iterations and out["reply"] == "Forced answer."


def test_number_guard_regenerates_then_flags(state):
    replies = iter(["You could save ₹99,999 a year.", "You could save ₹88,888 a year."])

    def brain(req):
        if req.task == "agent" and not tool_messages(req):
            return ChatResult(content="", tool_calls=[ToolCall("c1", "budget_check", "{}")])
        return ChatResult(content=next(replies))

    out = agent_with(state, brain).handle("how are my budgets?")
    assert not out["verified"]
    assert "couldn't verify these figures" in out["reply"] and "₹88,888" in out["reply"]
    assert [c["task"] for c in out["calls"] if c["task"] != "extract_memory"][-1] == "guard_retry"


def test_all_models_down_gives_a_friendly_error(state):
    from ledgermind.llm import LLMError

    def brain(req):
        raise LLMError("down", retryable=False)

    state.router.sleep = lambda _: None
    out = agent_with(state, brain).handle("How much did I spend last month?")
    assert "error" in out and "try again" in out["reply"].lower()


def test_agent_payloads_carry_no_pii_or_raw_rows(state):
    Agent(state).handle("My email is a.b@example.com and I paid Rahul Verma 5000. What did I spend on shopping?")
    payloads = [json.dumps(r.messages, ensure_ascii=False) for _, r in state.router.client.requests]
    for leak in ("a.b@example.com", "Rahul Verma", "UPI/", "fingerprint", "XXXX"):
        assert all(leak not in p for p in payloads), leak
    inspector = state.privacy.last_turn()
    assert inspector["calls"] and inspector["calls"][0]["redactions"].get("email") == 1


def test_strict_mode_withholds_rows_from_the_model(state):
    state.settings.privacy.strict_mode = True
    out = Agent(state).handle("Anything unusual lately?")
    view = out["tool_results"]["detect_anomalies"]
    assert view["anomalies"]["detail"] == "withheld in strict privacy mode" and view["anomalies"]["count"] >= 3
    withheld = [w for c in state.privacy.last_turn()["calls"] for w in c["withheld"]]
    assert any("strict" in w for w in withheld)
    assert isinstance(out["tool_results_local"]["detect_anomalies"]["anomalies"], list)


def test_memory_is_learned_and_used_next_turn(state):
    first = Agent(state).handle("I'm vegetarian and I usually cook at home on weekdays. How much on groceries?")
    assert first["learned"]
    second = Agent(state).handle("Anything about my groceries?")
    assert any("cook" in m["content"].lower() or "vegetarian" in m["content"].lower()
               for m in second["memories_used"])


# ---------------------------------------------------------------- monthly review

def test_monthly_review_and_markdown_export(state):
    out = run_skill(state.ctx, "monthly_review", {"month": "2026-09"})
    assert len(out["report"]["actions"]) == 3 and out["export_markdown"] == "/api/review/2026-09.md"
    md = review_markdown(state.ctx, "2026-09")
    assert md.startswith("# Ledgermind monthly review: September 2026")
    assert out["figures"]["income"] and "not financial advice" in md


def test_review_rejects_model_numbers_it_cannot_verify(state):
    bad = json.dumps({"headline": "You saved ₹12,34,567!", "wins": ["a"], "risks": ["b"], "actions": ["1", "2", "3"]})
    good_month = "2026-08"
    state.router.client.brain = lambda req: ChatResult(content=bad) if req.task == "monthly_review" \
        else mock_brain(req)
    out = run_skill(state.ctx, "monthly_review", {"month": good_month})
    assert out["written_by"] == "deterministic template" and "12,34,567" not in json.dumps(out["report"])
