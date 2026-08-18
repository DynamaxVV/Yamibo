from __future__ import annotations

from unittest.mock import patch

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
    assert "table_layouts" in data["values"]


def test_settings_exposes_hermes_connection_fields_without_secret(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["values"]["hermes_host"] == "127.0.0.1"
    assert body["values"]["hermes_port"] == 8642
    assert body["values"]["hermes_model"] == "hermes-agent"
    assert body["values"]["hermes_api_key"] is None
    assert body["stored"]["hermes_api_key"] is None
    assert body["values"]["chat_streaming_enabled"] is True


def test_settings_update_persists_hermes_connection_fields(client, test_settings):
    import json

    response = client.post(
        "/api/settings",
        json={"values": {"hermes_host": "127.0.0.1", "hermes_port": 8643, "hermes_model": "hermes-test", "chat_streaming_enabled": False}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["restart_targets"] == ["web"]
    assert body["effect_mode_summary"] == "restart_web"
    saved = json.loads(test_settings.config_path.read_text())
    assert saved["chat"] == {"hermes_host": "127.0.0.1", "hermes_port": 8643, "hermes_model": "hermes-test"}
    assert saved["ui"]["chat_streaming_enabled"] is False


def test_settings_update_persists_table_layout(client, test_settings):
    layouts = {
        "threads": [{"key": "title", "visible": True, "width": 620}],
        "jobs": [{"key": "description", "visible": True, "width": 520}],
    }
    resp = client.post("/api/settings", json={"values": {"table_layouts": layouts}})
    assert resp.status_code == 200
    saved = resp.json()["values"]["table_layouts"]
    assert next(item for item in saved["threads"] if item["key"] == "title")["width"] == 620
    assert next(item for item in saved["jobs"] if item["key"] == "description")["width"] == 520


def test_settings_sensitive_values_are_redacted(client, test_settings, monkeypatch):
    sentinel = "TEST_ONLY_SECRET_SENTINEL"
    test_settings.config_path.write_text('{"llm": {"api_key": "' + sentinel + '"}, "chat": {"hermes_api_key": "' + sentinel + '"}}')
    monkeypatch.setenv("YAMIBO_LLM_API_KEY", sentinel)
    monkeypatch.setenv("YAMIBO_HERMES_API_KEY", sentinel)
    response = client.get("/api/settings")
    body = response.json()
    assert sentinel not in str(body)
    assert body["values"]["llm_api_key"] is None and body["stored"]["llm_api_key"] is None
    assert body["configured"]["llm_api_key"] is True and body["configured"]["rag_api_key"] is True
    assert body["configured"]["hermes_api_key"] is True
    for name, value in body["values"].items():
        if name.endswith("api_key") or name == "db_url":
            assert value is None


def test_settings_post_secret_does_not_echo(client, test_settings):
    sentinel = "POST_ONLY_SECRET_SENTINEL"
    response = client.post("/api/settings", json={"values": {"llm_api_key": sentinel}})
    assert response.status_code == 200
    assert sentinel not in str(response.json())
    assert response.json()["values"]["llm_api_key"] is None
    assert response.json()["configured"]["llm_api_key"] is True


def test_settings_hermes_test_returns_probe_payload(client):
    from yamibo_mcp.web_fastapi.deps import get_chat_service
    class Service:
        def context(self, **kwargs): return {"ready": True, "hermes": {"connected": True}}
    client.app.dependency_overrides[get_chat_service] = lambda: Service()
    try:
        resp = client.post("/api/settings/hermes-test", json={})
    finally:
        client.app.dependency_overrides.pop(get_chat_service, None)
    assert resp.status_code == 200
    assert resp.json()["hermes"]["connected"] is True


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


def test_forums_sign_in_stats_returns_configured_accounts(client):
    with patch(
        "yamibo_mcp.web_fastapi.routers.forums._daily_sign_in_stats",
        return_value={"fetched_at": "2026-08-16T00:00:00+00:00", "accounts": []},
    ):
        resp = client.get("/api/forums/sign-in-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "fetched_at" in data
    assert isinstance(data["accounts"], list)


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
    resp = client.get("/api/chat/context")
    assert resp.status_code == 200
    assert resp.json()["transport"] == "hermes_runs"


def test_chat_turn_removed(client):
    assert client.post("/api/chat/turn", json={"message": "hello"}).status_code == 404


def test_remote_forums_list(client):
    """GET /api/remote/forums returns enabled forums."""
    resp = client.get("/api/remote/forums")
    assert resp.status_code == 200


def test_remote_image_proxy_rejects_localhost(client):
    resp = client.get("/api/remote/image", params={"url": "http://127.0.0.1:8000/a.png"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "only public http(s) image URLs are supported"
