from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yamibo_mcp.web_fastapi.deps import get_conn
from yamibo_mcp.web_fastapi.settings_session import install_settings_sessions
from yamibo_mcp.web_fastapi.routers import scheduled_tasks


class FakeConnection:
    def commit(self):
        pass

    def rollback(self):
        pass


class FakeRepository:
    rows = {}

    def __init__(self, conn):
        pass

    def list(self, owner_id):
        assert owner_id == "local"
        return list(self.rows.values())

    def create(self, **kwargs):
        item = {**kwargs, "task_id": "task-1", "revision": 1}
        self.rows[item["task_id"]] = item
        return item

    def get(self, task_id, owner_id):
        return self.rows.get(task_id)

    def update(self, task_id, expected_revision, **kwargs):
        item = self.rows.get(task_id)
        if item is None:
            return None
        if item["revision"] != expected_revision:
            raise ValueError("REVISION_CONFLICT")
        item.update(kwargs, revision=expected_revision + 1)
        return item

    def occurrences(self, task_id, owner_id):
        return []

    def delete(self, task_id, *, owner_id, expected_revision):
        item = self.rows.get(task_id)
        if item is None or item["revision"] != expected_revision:
            return False
        item["archived"] = True
        return True


def test_settings_auth_and_schedule_validation(monkeypatch):
    FakeRepository.rows = {}
    monkeypatch.setattr(scheduled_tasks, "ScheduledTasksRepository", FakeRepository)
    def fake_trigger(conn, *, task_id, owner_id, expected_revision, now):
        if expected_revision != FakeRepository.rows[task_id]["revision"]:
            raise ValueError("REVISION_CONFLICT")
        return {"task_id": task_id, "scheduled_at": now, "status": "queued", "result": {"job_id": "job-1"}}
    monkeypatch.setattr(scheduled_tasks, "trigger_task", fake_trigger)
    app = FastAPI()
    app.include_router(scheduled_tasks.router)
    install_settings_sessions(app, SimpleNamespace(settings_access_token="test-token", chat_access_token=None))
    app.dependency_overrides[get_conn] = lambda: FakeConnection()
    body = {
        "name": "归档指定帖", "action": "archive_thread", "arguments": {"tid": 572313, "mode": "text_only"},
        "schedule_kind": "cron", "cron": "0 8 * * *", "timezone": "Asia/Shanghai", "enabled": True,
    }
    with TestClient(app) as client:
        assert client.get("/api/settings/scheduled-tasks").status_code == 401
        assert client.post("/api/settings/scheduled-tasks", json=body,
                           headers={"origin": "http://testserver"}).status_code == 401
        assert client.post("/api/settings/session", json={"token": "test-token"},
                           headers={"origin": "http://testserver"}).status_code == 200
        assert client.post("/api/settings/scheduled-tasks", json=body,
                           headers={"origin": "https://evil.example"}).status_code == 403
        client.headers["origin"] = "http://testserver"
        assert client.post("/api/settings/scheduled-tasks", json={**body, "action": "shell"}).status_code == 422
        created = client.post("/api/settings/scheduled-tasks", json=body)
        assert created.status_code == 201
        assert created.json()["arguments"]["tid"] == 572313
        assert len(client.get("/api/settings/scheduled-tasks").json()["items"]) == 1
        assert client.get("/api/settings/scheduled-tasks/task-1/runs").json() == {"items": []}
        assert client.put("/api/settings/scheduled-tasks/task-1", json={**body, "expected_revision": 999}).status_code == 409
        assert client.put("/api/settings/scheduled-tasks/task-1", json={**body, "expected_revision": 1, "enabled": False}).json()["enabled"] is False
        assert client.post("/api/settings/scheduled-tasks/task-1/trigger", json={"expected_revision": 1}).status_code == 409
        triggered = client.post("/api/settings/scheduled-tasks/task-1/trigger", json={"expected_revision": 2})
        assert triggered.json()["job_id"] == "job-1"
        assert scheduled_tasks._run({"status": "failed", "result": {"error_code": "REMOTE_ACCESS_PAUSED"}})["error"] == "REMOTE_ACCESS_PAUSED"
        assert client.post("/api/settings/scheduled-tasks/task-1/archive", json={"expected_revision": 1}).status_code == 409
        assert client.post("/api/settings/scheduled-tasks/task-1/archive", json={"expected_revision": 2}).json()["archived"] is True
