from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from yamibo_mcp.web_fastapi.app import create_app
from tests.unit.test_web_fastapi.conftest import _TestSettings
from yamibo_mcp.db.repositories.jobs import JobsRepository


def test_dashboard_returns_expected_keys(client):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "thread_count" in data
    assert "series_count" in data
    assert "recent_jobs" in data
    assert "workers" in data


def test_health_returns_runtime_info(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "database" in data
    assert "data_dir" in data


def test_jobs_list_returns_paginated(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert "items" in data


def test_jobs_list_filters_other_failure_kind(client, test_settings):
    from yamibo_mcp.db.connection import connect

    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=42)
        repo.fail(job.job_id, "INTERNAL_ERROR", "opaque failure")
    finally:
        conn.close()

    resp = client.get("/api/jobs", params={"status": "failed", "failure_kind": "other"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert data["items"][0]["job_id"] == job.job_id


def test_job_not_found_returns_404(client):
    resp = client.get("/api/jobs/nonexistent")
    assert resp.status_code == 404


def test_delete_job_missing_id_returns_422(client):
    resp = client.post("/api/jobs/delete", json={})
    assert resp.status_code == 422


def test_spa_fallback_returns_html_when_static_exists(test_settings):
    import os
    static_dir = test_settings.data_dir.parent / "static_override"
    static_dir.mkdir(exist_ok=True)
    (static_dir / "index.html").write_text("<!doctype html><html></html>")
    os.environ["YAMIBO_STATIC_DIR"] = str(static_dir)
    try:
        from yamibo_mcp.web_fastapi.app import create_app
        from fastapi.testclient import TestClient
        app = create_app(test_settings)
        c = TestClient(app)
        resp = c.get("/settings")
        assert resp.status_code == 200
        assert b"html" in resp.content
    finally:
        del os.environ["YAMIBO_STATIC_DIR"]


def test_api_not_found_returns_404(client):
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404


def test_settings_get_returns_config(client, test_settings):
    """GET /api/settings returns config payload."""
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "values" in data
    assert "sources" in data
    assert "config_path" in data


def test_settings_hermes_test_returns_probe_payload(client):
    with patch(
        "yamibo_mcp.web_fastapi.routers.settings.probe_hermes_chat_completions",
        return_value={"connected": True, "endpoint": "http://127.0.0.1:8642/v1/chat/completions", "label": "ok", "status": 200, "stdout": "", "stderr": ""},
    ):
        resp = client.post("/api/settings/hermes-test", json={})
    assert resp.status_code == 200
    assert resp.json()["connected"] is True


def test_thread_detail_not_found(client):
    """GET /api/threads/{tid} returns 404 for missing thread."""
    resp = client.get("/api/threads/999999")
    assert resp.status_code == 404


def test_series_list(client):
    """GET /api/series returns list."""
    resp = client.get("/api/series")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_forums_list(client):
    """GET /api/forums returns list."""
    resp = client.get("/api/forums")
    assert resp.status_code == 200


def test_daemon_status(client):
    """GET /api/daemon/status returns operational mode."""
    resp = client.get("/api/daemon/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "operational_mode" in data


def test_debug_info(client):
    """GET /api/debug/info returns stats."""
    resp = client.get("/api/debug/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "stats" in data


def test_rag_overview(client):
    """GET /api/rag/overview returns RAG config."""
    resp = client.get("/api/rag/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data


def test_review_items(client):
    """GET /api/review returns review items."""
    resp = client.get("/api/review")
    assert resp.status_code == 200
    data = resp.json()
    assert "titles" in data
    assert "series" in data


def test_exports_list(client):
    """GET /api/exports returns export list."""
    resp = client.get("/api/exports")
    assert resp.status_code == 200


def test_media_missing_file_returns_404(client):
    """GET /media/nonexistent returns 404."""
    resp = client.get("/media/nonexistent/path/file.jpg")
    assert resp.status_code == 404


def test_fonts_list(client):
    """GET /api/fonts returns font list."""
    resp = client.get("/api/fonts")
    assert resp.status_code == 200


def test_logs_endpoint(client):
    """GET /api/logs returns log entries."""
    resp = client.get("/api/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data


def test_chat_context_returns_runtime_payload(client):
    with patch(
        "yamibo_mcp.web_fastapi.routers.chat.get_chat_context",
        return_value={"transport": "hermes_http", "daemon_connected": True},
    ):
        resp = client.get("/api/chat/context")
    assert resp.status_code == 200
    assert resp.json()["transport"] == "hermes_http"


def test_chat_turn_executes_non_stream(client):
    with patch(
        "yamibo_mcp.web_fastapi.routers.chat.run_chat_turn",
        return_value={"assistant_message": "done", "commands": [], "warnings": [], "transport": "hermes_http"},
    ):
        resp = client.post("/api/chat/turn", json={"message": "hello", "history": []})
    assert resp.status_code == 200
    assert resp.json()["assistant_message"] == "done"


def test_chat_turn_streams_sse(client):
    events = [{"type": "delta", "content": "hi"}, {"type": "done", "status": 200}]
    with patch(
        "yamibo_mcp.web_fastapi.routers.chat.iter_chat_turn_stream",
        return_value=iter(events),
    ):
        with client.stream("POST", "/api/chat/turn", json={"message": "hello", "stream": True}) as resp:
            body = b"".join(resp.iter_bytes())
    assert resp.status_code == 200
    assert b"data: [DONE]" in body
    assert b"\"type\": \"delta\"" in body


def test_chat_session_delete_calls_service(client):
    with patch(
        "yamibo_mcp.web_fastapi.routers.chat.delete_chat_session",
        return_value={"ok": True, "deleted": 1},
    ):
        resp = client.delete("/api/chat/sessions/chat-1")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1


def test_remote_forums_list(client):
    """GET /api/remote/forums returns enabled forums."""
    resp = client.get("/api/remote/forums")
    assert resp.status_code == 200


def test_remote_image_proxy_rejects_localhost(client):
    resp = client.get("/api/remote/image", params={"url": "http://127.0.0.1:8000/a.png"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "only public http(s) image URLs are supported"
