"""HTTP API: status, demo, streaming chat, memory panel, Privacy Inspector, review export and CSV upload."""
import json

import pytest
from fastapi.testclient import TestClient

from ledgermind import server
from ledgermind.config import load_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "settings", load_settings(env={"LEDGERMIND_MOCK": "1",
                                                               "LEDGERMIND_DB": str(tmp_path / "api.db")}))
    monkeypatch.setattr(server, "_states", {})
    return TestClient(server.app)


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_status_and_demo(client):
    status = client.get("/api/status").json()
    assert status["mock"] and status["ledger"]["transactions"] == 0
    assert status["roles"]["agent"][0]["id"] == "nvidia/nemotron-3-super-120b-a12b"
    assert client.get("/api/dashboard").json() == {"empty": True}
    loaded = client.post("/api/demo").json()
    assert loaded["added"] > 600 and "_skipped_examples" in loaded
    dash = client.get("/api/dashboard").json()
    assert len(dash["trend"]) == 12 and len(dash["subscriptions"]["price_hikes"]) >= 1


def test_chat_stream_emits_tool_steps_and_badges(client):
    client.post("/api/demo")
    res = client.post("/api/chat/stream", json={"message": "Find my subscriptions"})
    assert res.headers["content-type"].startswith("text/event-stream")
    events = sse_events(res.text)
    kinds = [k for k, _ in events]
    assert kinds[:2] == ["turn", "memories"] and kinds[-1] == "done"
    assert kinds.index("tool_call") < kinds.index("tool_result") < kinds.index("token") < kinds.index("answer")
    done = events[-1][1]
    assert done["answer_model"] == "Nemotron 3 Super" and done["calls"][0]["latency_ms"] > 0
    history = client.get("/api/history").json()
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_privacy_inspector_and_strict_toggle(client):
    client.post("/api/demo")
    client.post("/api/chat", json={"message": "My UPI is me@okicici. How much did I spend on dining last month?"})
    last = client.get("/api/privacy/last").json()
    sent = json.dumps([c["payload"] for c in last["calls"]])
    assert "me@okicici" not in sent and last["calls"][0]["redactions"].get("upi_id") == 1
    assert last["never_sent"]["transactions_in_local_ledger"] > 600
    assert client.post("/api/privacy/strict", json={"enabled": True}).json() == {"strict_mode": True}
    assert client.get("/api/status").json()["strict_mode"] is True


def test_memory_crud(client):
    created = client.post("/api/memories", json={"content": "I prefer short answers", "type": "preference"}).json()
    mid = created["memory"]["id"]
    assert client.patch(f"/api/memories/{mid}", json={"pinned": True, "content": "Prefers short answers"}).json()[
        "pinned"]
    assert [m["content"] for m in client.get("/api/memories").json()["memories"]] == ["Prefers short answers"]
    assert client.delete(f"/api/memories/{mid}").json() == {"ok": True}
    assert client.patch(f"/api/memories/{mid}", json={"pinned": False}).status_code == 404
    assert client.post("/api/memories", json={"content": "x", "type": "nonsense"}).status_code == 400


def test_review_export(client):
    client.post("/api/demo")
    assert client.get("/api/review/2026-09.md").status_code == 404
    client.post("/api/chat", json={"message": "Give me a monthly review for September"})
    md = client.get("/api/review/2026-09.md")
    assert md.status_code == 200 and md.text.startswith("# Ledgermind monthly review")
    assert client.get("/api/review/sept.md").status_code == 400


def test_csv_upload_and_friendly_errors(client):
    csv = "Date,Description,Amount\n2026-10-01,Swiggy order,-450\n2026-10-02,Salary ACME,90000\n"
    ok = client.post("/api/import", files={"file": ("bank.csv", csv, "text/csv")}).json()
    assert ok["added"] == 2
    again = client.post("/api/import", files={"file": ("bank.csv", csv, "text/csv")}).json()
    assert again["added"] == 0 and again["already_imported"] == 2
    bad = client.post("/api/import", files={"file": ("notes.csv", "hello world", "text/csv")})
    assert bad.status_code == 400 and "header row" in bad.json()["detail"]
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_export_and_wipe(client):
    client.post("/api/demo")
    data = client.get("/api/export").json()
    assert len(data["transactions"]) > 600
    client.post("/api/wipe")
    assert client.get("/api/status").json()["ledger"]["transactions"] == 0
