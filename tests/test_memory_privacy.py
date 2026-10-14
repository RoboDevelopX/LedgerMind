"""Typed memory (dedupe, retrieval, consolidation, remember/forget) and the privacy layer."""
import json

import pytest

from ledgermind.app import AppState
from ledgermind.config import load_settings
from ledgermind.llm import ChatRequest
from ledgermind.memory.consolidate import consolidate
from ledgermind.memory.extract import extract_memories, grounded
from ledgermind.memory.retrieve import retrieve
from ledgermind.money import money
from ledgermind.privacy.minimise import minimise
from ledgermind.privacy.redact import Redactor
from ledgermind.skills import run_skill

PII = ["a.b@example.com", "rahul.v@okaxis", "4111 1111 1111 1111", "50100234567890", "98765 43210",
       "ABCDE1234F", "HDFC0001234", "paid Rahul Verma"]


@pytest.fixture
def state(tmp_path):
    return AppState(settings=load_settings(env={"LEDGERMIND_MOCK": "1"}), db_path=str(tmp_path / "m.db"))


# ---------------------------------------------------------------- memory store

def test_dedupe_by_key_merges_and_keeps_history(state):
    store = state.memory
    a, act1 = store.upsert("profile", "Earns ₹1,40,000 a month.", key="income")
    b, act2 = store.upsert("profile", "Earns ₹1,55,000 a month after the April raise.", key="income")
    assert (act1, act2) == ("created", "merged") and a["id"] == b["id"]
    assert b["data"]["history"] == ["Earns ₹1,40,000 a month."] and store.count() == 1
    _, act3 = store.upsert("profile", "Earns ₹1,55,000 a month after the April raise.", key="income")
    assert act3 == "unchanged"


def test_model_paraphrase_never_overwrites_a_user_memory(state):
    store = state.memory
    mine, _ = store.upsert("goal", "Saving for Japan trip: target ₹3,00,000 by 2027-03-31.", key="japan trip",
                           confidence=1.0, source="user")
    after, action = store.upsert("goal", "Target ₹3,00,000 by 2027-03-31", key="japan trip", source="chat")
    assert action == "unchanged" and after["id"] == mine["id"]
    assert "Japan" in after["content"] and after["source"] == "user"
    # A new amount the user states later still updates it.
    raised, action = store.upsert("goal", "Saving for Japan trip: target ₹4,00,000 by 2027-03-31.", key="japan trip",
                                  source="chat")
    assert action == "merged" and "4,00,000" in raised["content"]


@pytest.mark.parametrize("content,user,ok", [
    ("Saving ₹3,00,000 for a Japan trip by 2027-03-31.", "I'm saving ₹3 lakh for a Japan trip by March", True),
    ("Saving ₹50,000 for a new laptop.", "Also I'm saving 50k for a new laptop by December.", True),
    ("Pays ₹28,000 rent on the 1st.", "My rent is 28,000 and I pay it on the 1st.", True),
    ("Lives with wife and two kids.", "I live in Pune with my wife and our two kids.", True),
    ("Dining spend averages ₹18,684 a month.", "How much did I spend on dining last month?", False),
    ("Lives in Mumbai.", "What do you remember about me?", False),
    ("₹", "What was my income in September?", False),
])
def test_extracted_memories_must_be_grounded_in_the_users_words(content, user, ok):
    assert grounded(content, user) is ok


def test_dedupe_by_similarity(state):
    store = state.memory
    store.upsert("preference", "Prefers short answers with bullet points.")
    _, action = store.upsert("preference", "Prefers short answers, with bullet points please.")
    assert action == "merged" and store.count() == 1
    store.upsert("preference", "Is vegetarian.")
    assert store.count() == 2


def test_retrieval_blends_relevance_importance_recency(state):
    store = state.memory
    store.upsert("profile", "Gets paid on the last working day.", key="payday", importance=0.9)
    store.upsert("goal", "Saving for a Japan trip in March.", key="japan trip", importance=0.8)
    store.upsert("habit", "Orders food delivery most Fridays.", importance=0.4)
    for i in range(8):
        store.upsert("decision", f"Decision number {i} about electricity bills.", importance=0.2)
    top = retrieve(store, "how is my japan trip saving going", k=3)
    assert top[0]["content"].startswith("Saving for a Japan trip")
    assert any(m["type"] == "profile" for m in top)  # profile is always a candidate
    assert store.get(top[0]["id"])["use_count"] == 1


def test_extraction_from_conversation(state):
    applied = extract_memories(state.router, state.memory, state.settings,
                               "I earn ₹1.4 lakh a month. I'm saving for a Japan trip. I prefer short answers.")
    kinds = sorted(a["memory"]["type"] for a in applied)
    assert kinds == ["goal", "preference", "profile"]
    assert extract_memories(state.router, state.memory, state.settings, "what did I spend on dining?") == []


def test_consolidation_merges_prunes_and_protects_user_memories(state):
    store = state.memory
    store.upsert("habit", "Orders food delivery on Fridays.", importance=0.6)
    state.db.execute("INSERT INTO memories(type, key, content, importance, source, created_at, updated_at) "
                     "VALUES ('habit','dup','Orders food delivery on Fridays nights.',0.5,'chat',"
                     "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
    store.upsert("decision", "Old low value note.", importance=0.1)
    state.db.execute("UPDATE memories SET updated_at='2025-01-01T00:00:00+00:00' WHERE content LIKE 'Old%'")
    store.upsert("preference", "Never suggest crypto.", importance=0.1, source="user")
    state.db.execute("UPDATE memories SET updated_at='2025-01-01T00:00:00+00:00' WHERE content LIKE 'Never%'")
    for i, place in enumerate(["coffee shops", "bookstores", "petrol pumps", "cinemas", "bakeries", "gyms",
                               "airports", "pharmacies", "florists", "toy stores"]):
        store.upsert("habit", f"Spends a lot at {place} on weekends.", key=f"h{i}")
    report = consolidate(store, state.settings, state.router)
    assert report["merged"] == 1, report
    assert report["pruned"] == ["Old low value note."]
    assert report["summarised"]["habit"] == 4
    assert any(m["content"] == "Never suggest crypto." for m in store.list())


def test_remember_and_forget_skills(state):
    run_skill(state.ctx, "remember", {"content": "Wants to retire early.", "type": "goal"})
    run_skill(state.ctx, "remember", {"content": "Salary is credited to the HDFC account."})
    assert state.memory.count() == 2
    gone = run_skill(state.ctx, "forget", {"about": "my salary"})
    assert gone["forgotten"] == ["Salary is credited to the HDFC account."] and state.memory.count() == 1
    assert run_skill(state.ctx, "recall_memories", {})["memories"][0]["content"] == "Wants to retire early."


# ---------------------------------------------------------------- privacy

def test_redactor_masks_all_pii():
    text = ("Email a.b@example.com, UPI rahul.v@okaxis, card 4111 1111 1111 1111, acct 50100234567890, "
            "phone +91 98765 43210, PAN ABCDE1234F, IFSC HDFC0001234. I paid Rahul Verma ₹500 and paid Swiggy ₹450.")
    clean, counts = Redactor().redact(text)
    for item in PII:
        assert item not in clean
    assert "₹500" in clean and "Swiggy ₹450" in clean
    assert counts["name"] == 1 and counts["card"] == 1 and counts["account"] == 1


def test_minimise_drops_ids_caps_rows_and_strict_mode():
    result = {"total": money(1500), "_raw": ["secret"], "rows": [
        {"id": i, "date": "2026-10-01", "merchant": "Swiggy", "amount": money(100), "description": "UPI/..."}
        for i in range(12)]}
    view, withheld = minimise(result, strict=False, max_rows=8, currency="INR")
    assert view["total"] == "₹1,500" and "_raw" not in view and len(view["rows"]) == 8
    assert "id" not in view["rows"][0] and "description" not in view["rows"][0]
    strict_view, strict_withheld = minimise(result, strict=True, max_rows=8, currency="INR")
    assert strict_view["rows"] == {"count": 12, "detail": "withheld in strict privacy mode"}
    assert any("strict" in w for w in strict_withheld)


def test_outgoing_payloads_are_redacted_and_logged(state):
    state.router.chat("fast", ChatRequest(messages=[{"role": "user", "content": " ".join(PII)}], task="t",
                                          turn="turn-1"))
    state.privacy.note_withheld("turn-1", ["transactions (kept local)"])
    state.router.chat("fast", ChatRequest(messages=[{"role": "user", "content": "hello"}], task="t2",
                                          turn="turn-1"))
    sent = [m["content"] for m, _ in [(r[1].messages[0], 0) for r in state.router.client.requests]]
    for item in PII:
        assert all(item not in s for s in sent)
    inspector = state.privacy.last_turn()
    assert [c["task"] for c in inspector["calls"]] == ["t", "t2"]
    assert inspector["calls"][0]["redactions"]["card"] == 1
    assert inspector["calls"][1]["withheld"] == ["transactions (kept local)"]


def test_demo_import_sends_no_raw_descriptions(tmp_path):
    state = AppState(settings=load_settings(env={"LEDGERMIND_MOCK": "1"}), db_path=str(tmp_path / "p.db"))
    state.load_demo()
    payloads = " ".join(r["payload"] for r in state.db.all("SELECT payload FROM privacy_log"))
    assert payloads and "UPI/P2M" not in payloads and "@" not in payloads and "XXXX" not in payloads
    assert json.loads(state.db.all("SELECT payload FROM privacy_log")[0]["payload"])[1]["content"]
