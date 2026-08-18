from dataclasses import dataclass
from unittest.mock import Mock

from fastapi.testclient import TestClient

from yamibo_mcp.services.chat_runtime import ChatRun, ChatRunStatus, NormalizedChatEvent
from yamibo_mcp.services.web_chat import ChatServiceError
from yamibo_mcp.web_fastapi.app import create_app
from yamibo_mcp.web_fastapi.deps import get_chat_service


class FakeService:
    def __init__(self):
        self.run = ChatRun("r1", "s1", ChatRunStatus.RUNNING, 1, 2, last_seq=3)
        self.subscription = Mock()
    def context(self, **kw): return {"ready": True, "transport": "hermes_runs", "model": "m", "hermes": {"endpoint": "http://x", "connected": True, "version": "1", "has_api_key": True, "capabilities": []}}
    def list_sessions(self, **kw): return {"sessions": [], **kw}
    def create_session(self, **kw): return {"id": "s1", **kw}
    def get_session(self, sid): return {"id": sid}
    def update_session(self, sid, patch): return {"id": sid, **patch}
    def delete_session(self, sid): return {"deleted": sid}
    def get_messages(self, sid): return {"messages": []}
    def start_run(self, sid, text):
        if not text.strip(): raise ChatServiceError("CHAT_INVALID_REQUEST", "input required", 400)
        return self.run
    def get_run(self, rid): return self.run
    def stop_run(self, rid): return {"ok": True}
    def approve(self, rid, **kw): return kw
    def subscribe(self, rid, last_event_id=None): return self.subscription


def client_and_service(test_settings):
    app = create_app(test_settings); fake = FakeService(); app.dependency_overrides[get_chat_service] = lambda: fake
    return TestClient(app), fake


def test_routes_strict_inputs_and_run_response(test_settings):
    client, fake = client_and_service(test_settings)
    assert client.get("/api/chat/context").status_code == 200
    assert client.post("/api/chat/sessions", json={"title": "x", "history": []}).status_code == 400
    response = client.post("/api/chat/sessions/s1/runs", json={"input": "  "})
    assert response.status_code == 400 and response.json()["error"]["code"] == "CHAT_INVALID_REQUEST"
    response = client.post("/api/chat/sessions/s1/runs", json={"input": "hello"})
    assert response.status_code == 202 and response.json()["events_url"].endswith("/events")
    assert client.post("/api/chat/turn", json={}).status_code == 404


def test_get_run_is_complete_and_sse_reconciles(test_settings):
    client, fake = client_and_service(test_settings)
    response = client.get("/api/chat/runs/r1")
    assert response.status_code == 200
    assert {"run_id", "session_id", "created_at", "updated_at", "last_seq", "stop_requested", "terminal_payload", "error", "messages", "events_url"} <= response.json().keys()
    events = iter([NormalizedChatEvent("run.completed", "r1", 4, 1, {}), NormalizedChatEvent("session.reconciled", "r1", 5, 1, {"message_count": 1})])
    fake.subscription.get.side_effect = lambda timeout: next(events)
    with client.stream("GET", "/api/chat/runs/r1/events", headers={"Last-Event-ID": "3"}) as response:
        body = b"".join(response.iter_bytes()).decode()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
    assert "id: 4" in body and "event: run.completed" in body and "event: session.reconciled" in body
    assert fake.subscription.close.called


def test_sse_waits_for_reconciliation_after_stream_loss_and_ignores_unknown(test_settings):
    client, fake = client_and_service(test_settings)
    events = iter([
        NormalizedChatEvent("stream.error", "r1", 4, 1, {"error": {"code": "CHAT_UPSTREAM_EVENT_UNKNOWN"}}),
        NormalizedChatEvent("stream.error", "r1", 5, 1, {"error": {"code": "CHAT_RUN_STREAM_LOST"}}),
        NormalizedChatEvent("stream.error", "r1", 6, 1, {"error": {"code": "CHAT_RUN_CALIBRATION_MESSAGES_FAILED"}}),
    ])
    fake.subscription.get.side_effect = lambda timeout: next(events)
    with client.stream("GET", "/api/chat/runs/r1/events") as response:
        body = b"".join(response.iter_bytes()).decode()
    assert response.status_code == 200
    assert "event: CHAT_UPSTREAM_EVENT_UNKNOWN" not in body
    assert "id: 4" in body and "event: stream.error" in body and '"run_id":"r1"' in body
    assert "id: 6" in body
    assert fake.subscription.close.called
