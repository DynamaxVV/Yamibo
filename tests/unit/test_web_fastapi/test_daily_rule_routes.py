from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yamibo_mcp.web_fastapi.deps import get_conn
from yamibo_mcp.web_fastapi.routers import daily_rules


class FakeConnection:
    backend = "postgres"
    eligible_forums = {5, 6}

    def execute(self, _statement, params):
        class Result:
            def fetchall(self):
                return [
                    {"forum_id": forum_id, "content_kind": "discussion", "enabled": True}
                    for forum_id in params.values()
                    if forum_id in FakeConnection.eligible_forums
                ]
        return Result()

    def commit(self):
        pass

    def rollback(self):
        pass


class FakeRules:
    rows = {}

    def __init__(self, _conn):
        pass

    def create_rule(self, **kwargs):
        assert kwargs["owner_id"] == "owner-for-session"
        row = {"rule_id": "rule-1", "revision": 1, "next_run_at": datetime(2026, 9, 26, tzinfo=timezone.utc), "budget_json": kwargs["budget"], **kwargs}
        self.rows[row["rule_id"]] = row
        return row

    def get_rule(self, rule_id, *, owner_id):
        row = self.rows.get(rule_id)
        return row if row and row["owner_id"] == owner_id else None

    def list_rules(self, *, owner_id):
        return [row for row in self.rows.values() if row["owner_id"] == owner_id]

    def update_rule(self, *, rule_id, owner_id, expected_revision, **kwargs):
        row = self.get_rule(rule_id, owner_id=owner_id)
        if row is None or row["revision"] != expected_revision:
            raise ValueError("daily rule is missing, not owned by caller, or revision is stale")
        row.update(kwargs, revision=expected_revision + 1)
        return row


def client(monkeypatch):
    app = FastAPI()
    app.include_router(daily_rules.router)
    app.dependency_overrides[get_conn] = lambda: FakeConnection()
    monkeypatch.setattr(daily_rules, "DailyRulesRepository", FakeRules)
    monkeypatch.setattr(daily_rules, "_session_owner", lambda conn, session_id: "owner-for-session" if session_id == "known" else (_ for _ in ()).throw(daily_rules.DailyBriefError("CHAT_SESSION_NOT_FOUND", "missing", 404)))
    return TestClient(app)


def payload(**updates):
    return {
        "session_id": "known", "forum_ids": [5, 6], "timezone": "Asia/Shanghai",
        "execution_time": "08:30", "preparation_deadline": "08:00", "budget": {"top_n": 10},
        "enabled": True, **updates,
    }


def test_rule_routes_bind_owner_from_session_and_return_next_run(monkeypatch):
    FakeRules.rows = {}
    c = client(monkeypatch)
    created = c.post("/api/daily-rules", json=payload())
    assert created.status_code == 201
    body = created.json()
    assert body["owner_id"] == "owner-for-session"
    assert body["next_run_at"] == "2026-09-26T00:00:00+00:00"
    assert body["budget"] == {"top_n": 10}
    listed = c.get("/api/daily-rules?session_id=known")
    assert [item["rule_id"] for item in listed.json()["items"]] == ["rule-1"]
    assert c.get("/api/daily-rules?session_id=missing").status_code == 404


def test_rule_update_requires_revision_and_reports_stale_write(monkeypatch):
    FakeRules.rows = {}
    c = client(monkeypatch)
    assert c.post("/api/daily-rules", json=payload()).status_code == 201
    updated = c.put("/api/daily-rules/rule-1", json=payload(expected_revision=1, enabled=False))
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["enabled"] is False
    stale = c.put("/api/daily-rules/rule-1", json=payload(expected_revision=1, enabled=True))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "DAILY_RULE_REVISION_STALE"


def test_rule_contract_rejects_extra_or_untrusted_owner_fields(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/api/daily-rules", json=payload(owner_id="other")).status_code == 422
    assert c.post("/api/daily-rules", json=payload(forum_ids=[])).status_code == 422


def test_rule_rejects_missing_disabled_or_non_discussion_forums(monkeypatch):
    c = client(monkeypatch)
    response = c.post("/api/daily-rules", json=payload(forum_ids=[5, 99]))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DAILY_RULE_FORUM_NOT_ELIGIBLE"
