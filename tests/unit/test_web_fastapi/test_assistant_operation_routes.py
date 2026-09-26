from fastapi.testclient import TestClient

from yamibo_mcp.application.assistant_operation_plans import OperationPlanError
from yamibo_mcp.web_fastapi.app import create_app


def test_plan_approval_route_has_separate_frozen_contract(test_settings, monkeypatch):
    app = create_app(test_settings)
    monkeypatch.setattr(
        "yamibo_mcp.web_fastapi.routers.assistant_operations.create_operation_plan",
        lambda **kwargs: {"status": "awaiting_approval", "plan_hash": "a" * 64, "items": []},
    )
    monkeypatch.setattr(
        "yamibo_mcp.web_fastapi.routers.assistant_operations.approve_operation_plan",
        lambda **kwargs: {"status": "approved_pending_execution", "execution": "not_started"},
    )
    client = TestClient(app)
    draft = client.post(
        "/api/assistant/operation-plans",
        json={
            "session_id": "s", "run_id": "r", "request_key": "k", "action": "archive",
            "tids": [123], "require_images": True,
        },
    )
    assert draft.status_code == 201
    approved = client.post(
        "/api/assistant/operation-plans/plan-1/approve",
        json={"session_id": "s", "plan_version": 1, "plan_hash": "a" * 64},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved_pending_execution"
    assert approved.json()["execution"] == "not_started"
    widened = client.post(
        "/api/assistant/operation-plans/plan-1/approve",
        json={"session_id": "s", "plan_version": 1, "plan_hash": "a" * 64, "tids": [123, 456]},
    )
    assert widened.status_code == 422


def test_plan_errors_keep_machine_readable_codes(test_settings, monkeypatch):
    app = create_app(test_settings)
    def fail(**_kwargs):
        raise OperationPlanError("TID_OUTSIDE_SCOPE", "out of scope", 403)
    monkeypatch.setattr("yamibo_mcp.web_fastapi.routers.assistant_operations.create_operation_plan", fail)
    response = TestClient(app).post(
        "/api/assistant/operation-plans",
        json={
            "session_id": "s", "run_id": "r", "request_key": "k", "action": "archive",
            "tids": [123], "require_images": True,
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TID_OUTSIDE_SCOPE"


def test_plan_cancellation_has_separate_authenticated_contract(test_settings, monkeypatch):
    app = create_app(test_settings)
    monkeypatch.setattr(
        "yamibo_mcp.web_fastapi.routers.assistant_operations.cancel_operation_plan",
        lambda **kwargs: {"plan_id": "p", "status": "cancellation_requested"},
    )
    response = TestClient(app).post(
        "/api/assistant/operation-plans/p/cancel", json={"session_id": "s"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancellation_requested"


def test_plan_list_route_bounds_pagination_and_scopes_by_session(test_settings, monkeypatch):
    app = create_app(test_settings)
    calls = []
    monkeypatch.setattr(
        "yamibo_mcp.web_fastapi.routers.assistant_operations.list_operation_plans",
        lambda **kwargs: calls.append(kwargs) or {"plans": [], "limit": kwargs["limit"], "offset": kwargs["offset"], "has_more": False},
    )
    client = TestClient(app)
    response = client.get("/api/assistant/operation-plans?session_id=s&limit=5&offset=10")
    assert response.status_code == 200
    assert calls == [{"session_id": "s", "limit": 5, "offset": 10}]
    assert client.get("/api/assistant/operation-plans?session_id=s&limit=101").status_code == 422
    assert client.get("/api/assistant/operation-plans?session_id=s&offset=-1").status_code == 422
    assert client.get("/api/assistant/operation-plans?session_id=s&offset=10001").status_code == 422
