from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from yamibo_mcp.db.repositories.jobs import JobsRepository


def test_dashboard_returns_expected_keys(client):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "thread_count" in data
    assert "series_count" in data
    assert "recent_jobs" in data
    assert "workers" in data


def test_proxy_pool_health_returns_non_secret_report(client):
    resp = client.get("/api/proxy-pool/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["nodes"] == []
    assert "secret" not in data


def test_health_returns_runtime_info(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "database" in data
    assert "data_dir" in data


def test_media_route_exposes_only_image_roots(client, test_settings):
    image = test_settings.data_dir / "threads" / "42" / "images" / "cover.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"jpeg")
    cookie = test_settings.data_dir / "cookies" / "primary.cookie"
    cookie.parent.mkdir(parents=True)
    cookie.write_text("session=secret")

    assert client.get("/media/threads/42/images/cover.jpg").status_code == 200
    assert client.get("/media/cookies/primary.cookie").status_code == 404


def test_daemon_status_requires_a_fresh_worker_heartbeat(client, test_settings):
    from datetime import datetime, timedelta, timezone

    from yamibo_mcp.db.connection import connect
    from yamibo_mcp.db.repositories.system_state import SystemStateRepository

    conn = connect(test_settings.db_path)
    try:
        SystemStateRepository(conn).set_json(
            "worker_heartbeat:worker-1",
            {
                "worker_id": "worker-1",
                "status": "running",
                "heartbeat_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
    finally:
        conn.close()

    data = client.get("/api/daemon/status").json()
    assert data["daemon_alive"] is True
    assert data["worker_count"] == 1
    assert data["workers"][0]["worker_id"] == "worker-1"

    conn = connect(test_settings.db_path)
    try:
        SystemStateRepository(conn).set_json(
            "worker_heartbeat:worker-1",
            {
                "worker_id": "worker-1",
                "status": "running",
                "heartbeat_at": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="seconds"),
            },
        )
    finally:
        conn.close()
    stale = client.get("/api/daemon/status").json()
    assert stale["daemon_alive"] is False
    assert stale["worker_count"] == 0


def test_system_status_is_read_only_and_reports_unchecked_boundaries(client):
    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["database"]["ok"] is True
    assert "daemon" in data
    assert "reverse_proxy_auth" in data["not_checked"]


def test_jobs_list_returns_paginated(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert "items" in data


def test_running_job_does_not_expose_stale_error_as_active_failure(client, test_settings):
    from yamibo_mcp.db.connection import connect
    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=42)
        repo.acquire(job.job_id, "worker", 300)
        repo.fail(job.job_id, "REMOTE_SOFT_BLOCK", "old block")
        # Requeue through a fresh job is not needed to assert the converter contract;
        # acquire preserves the historical fields, while active_error is status-gated.
        conn.execute("UPDATE jobs SET status = 'running', finished_at = NULL")
        conn.commit()
    finally:
        conn.close()
    body = client.get(f"/api/jobs/{job.job_id}").json()
    assert body["status"] == "running"
    assert body["error_code"] == "REMOTE_SOFT_BLOCK"
    assert body["active_error"] is None
    assert body["failure_kind"] is None
    assert "retry_count" in body and "lease_until" in body


def test_retrying_job_exposes_remote_attempt_and_retry_metadata(client, test_settings):
    from yamibo_mcp.db.connection import connect
    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=42)
        repo.acquire(job.job_id, "worker", 300)
        repo.record_remote_attempt(job.job_id, {"node": "node-a", "account_id": "acct-a", "outcome": "soft_block"})
        repo.retry_later(job.job_id, error_code="REMOTE_SOFT_BLOCK", error_message="blocked", artifacts={
            "remote_attempt": {"node": "node-a", "account_id": "acct-a", "outcome": "soft_block"},
        }, delay_seconds=20)
    finally:
        conn.close()
    body = client.get(f"/api/jobs/{job.job_id}").json()
    assert body["status"] == "retrying"
    assert body["remote_attempt"]["node"] == "node-a"
    assert body["remote_attempt"]["account_id"] == "acct-a"
    assert body["retry_count"] == 1
    assert body["lease_until"]
    event_types = [event["event_type"] for event in client.get(f"/api/jobs/{job.job_id}/events").json()]
    assert "job.remote_attempt" in event_types


def test_deleted_prompt_in_remote_attempt_is_classified_as_thread_deleted(client, test_settings):
    from yamibo_mcp.db.connection import connect

    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=42)
        repo.acquire(job.job_id, "worker", 300)
        repo.fail(
            job.job_id,
            "REMOTE_SOFT_BLOCK",
            "legacy soft block",
            artifacts={
                "remote_attempt": {
                    "page_type": "unknown",
                    "prompt_text": "本帖已经删除，错误权限代码255",
                }
            },
        )
    finally:
        conn.close()

    body = client.get(f"/api/jobs/{job.job_id}").json()
    assert body["failure_kind"] == "thread_deleted"


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


def test_job_events_support_incremental_cursor_headers(client, test_settings):
    from yamibo_mcp.db.connection import connect
    from yamibo_mcp.db.repositories.jobs import JobsRepository

    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=42)
        repo.update_stage(job.job_id, "fetching")
    finally:
        conn.close()

    first = client.get(f"/api/jobs/{job.job_id}/events", params={"limit": 1})
    assert first.status_code == 200
    assert first.headers["X-Has-More"] == "true"
    cursor = first.headers["X-Next-Event-ID"]
    later = client.get(f"/api/jobs/{job.job_id}/events", params={"since_event_id": cursor})
    assert later.status_code == 200
    assert all(event["event_id"] > int(cursor) for event in later.json())


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


def test_retry_thread_image_creates_selected_job_and_keeps_history(client, test_settings):
    from yamibo_mcp.db.connection import connect

    tid = 47901
    asset_id = "asset-retry-47901"
    remote_url = "https://bbs.yamibo.com/data/attachment/forum/2026/target-23.jpg"
    conn = connect(test_settings.db_path)
    try:
        conn.execute("INSERT INTO threads (tid, raw_title, display_title) VALUES (?, ?, ?)", (tid, "raw", "display"))
        conn.execute(
            "INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, status) VALUES (?, ?, ?, ?, ?, ?)",
            (asset_id, tid, tid * 10 + 1, "image", remote_url, "missing"),
        )
        old = JobsRepository(conn).create("sync_thread", tid=tid, payload={"tid": tid})
        JobsRepository(conn).succeed(old.job_id)
    finally:
        conn.close()

    response = client.post(f"/api/threads/{tid}/images/{asset_id}/retry")
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    duplicate = client.post(f"/api/threads/{tid}/images/{asset_id}/retry")
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "ok": True,
        "job_id": body["job_id"],
        "status": "queued",
        "created": False,
    }
    conn = connect(test_settings.db_path)
    try:
        row = conn.execute("SELECT payload_json FROM jobs WHERE job_id = ?", (body["job_id"],)).fetchone()
        payload = json.loads(row["payload_json"])
        assert payload == {
            "tid": tid,
            "dry_run": False,
            "scope": "selected",
            "target_asset_id": asset_id,
            "target_urls": [remote_url],
            "include_first_floor": True,
            "priority": "interactive",
        }
        assert conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (old.job_id,)).fetchone() is not None
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE job_type = 'image_backfill' AND tid = ?",
            (tid,),
        ).fetchone()["n"] == 1
    finally:
        conn.close()


def test_thread_image_retry_matches_rotated_attachment_signature(client, test_settings):
    from yamibo_mcp.db.connection import connect

    tid = 575090
    pid = 41605934
    asset_id = "asset-rotated-575090"
    old_url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHw1MWU2OTRkM3wxNzg3MjIyNzU3fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    current_url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHxmYjQzZDc3ZnwxNzg3MzI5ODk5fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    conn = connect(test_settings.db_path)
    try:
        conn.execute("INSERT INTO threads (tid, raw_title, display_title) VALUES (?, ?, ?)", (tid, "raw", "display"))
        conn.execute(
            "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, 1, 'first', 1)",
            (pid, tid),
        )
        conn.execute(
            "INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, status) VALUES (?, ?, ?, ?, ?, ?)",
            (asset_id, tid, pid, "attachment", old_url, "missing"),
        )
        conn.commit()
    finally:
        conn.close()

    metadata_path = test_settings.data_dir / "threads" / str(tid) / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps({
        "floors": [{
            "pid": pid,
            "floor_no": 1,
            "remote_image_urls": [current_url],
            "image_slots": [{"remote_url": current_url, "local_path": None, "status": "missing"}],
        }],
    }), encoding="utf-8")

    detail = client.get(f"/api/threads/{tid}")
    assert detail.status_code == 200
    assert detail.json()["floors"][0]["image_slots"][0]["asset_id"] == asset_id

    retry = client.post(f"/api/threads/{tid}/images/{asset_id}/retry")
    assert retry.status_code == 200
    conn = connect(test_settings.db_path)
    try:
        row = conn.execute("SELECT payload_json FROM jobs WHERE job_id = ?", (retry.json()["job_id"],)).fetchone()
        payload = json.loads(row["payload_json"])
        assert payload["target_positions"] == [{
            "pid": pid,
            "floor_no": 1,
            "image_index": 1,
            "url": old_url,
        }]
    finally:
        conn.close()


def test_active_sync_job_is_lightweight_and_excludes_terminal_jobs(client, test_settings):
    from yamibo_mcp.db.connection import connect

    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=4242, payload={"tid": 4242})
    finally:
        conn.close()

    response = client.get("/api/threads/4242/active-sync-job")
    assert response.status_code == 200
    assert response.json() == {
        "job": {
            "job_id": job.job_id,
            "job_type": "sync_thread",
            "tid": 4242,
            "status": "queued",
            "stage": None,
            "updated_at": job.updated_at,
        }
    }
    assert "payload" not in response.json()["job"]
    assert "artifacts" not in response.json()["job"]

    conn = connect(test_settings.db_path)
    try:
        JobsRepository(conn).succeed(job.job_id)
    finally:
        conn.close()
    assert client.get("/api/threads/4242/active-sync-job").json() == {"job": None}


def test_active_sync_job_returns_null_for_unknown_tid(client):
    response = client.get("/api/threads/987654/active-sync-job")
    assert response.status_code == 200
    assert response.json() == {"job": None}


@pytest.mark.parametrize(
    "status",
    ["queued", "running", "retrying", "paused", "interrupted", "cancel_requested"],
)
def test_active_sync_job_returns_each_live_status(client, test_settings, status):
    from yamibo_mcp.db.connection import connect

    tid = 5000 + ["queued", "running", "retrying", "paused", "interrupted", "cancel_requested"].index(status)
    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=tid, payload={"tid": tid})
        conn.execute("UPDATE jobs SET status = ?, stage = ? WHERE job_id = ?", (status, "parse", job.job_id))
        conn.commit()
    finally:
        conn.close()

    response = client.get(f"/api/threads/{tid}/active-sync-job")
    assert response.status_code == 200
    assert response.json()["job"] == {
        "job_id": job.job_id,
        "job_type": "sync_thread",
        "tid": tid,
        "status": status,
        "stage": "parse",
        "updated_at": response.json()["job"]["updated_at"],
    }


@pytest.mark.parametrize("status", ["succeeded", "failed", "partial"])
def test_active_sync_job_excludes_each_terminal_status(client, test_settings, status):
    from yamibo_mcp.db.connection import connect

    tid = 5100 + ["succeeded", "failed", "partial"].index(status)
    conn = connect(test_settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create("sync_thread", tid=tid, payload={"tid": tid})
        conn.execute("UPDATE jobs SET status = ? WHERE job_id = ?", (status, job.job_id))
        conn.commit()
    finally:
        conn.close()

    response = client.get(f"/api/threads/{tid}/active-sync-job")
    assert response.status_code == 200
    assert response.json() == {"job": None}


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


def test_forums_sign_in_accounts_returns_roster_before_remote_fetch(client):
    resp = client.get("/api/forums/sign-in-accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["accounts"]) == 1
    assert data["accounts"][0]["account_id"] == "default"


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
    assert "applied_filters" in data
    assert "oldest_ts" in data
    assert "newest_ts" in data


def test_logs_endpoint_filters_structured_image_diagnostics(client):
    import logging

    from yamibo_mcp.structured_logging import emit

    job_id = "image_backfill_log_filter_test"
    emit(
        logging.getLogger("yamibo_mcp.tests.image_logs"),
        logging.WARNING,
        "image.download.result",
        "Image download failed",
        result="failure",
        status="missing",
        error_code="IMAGE_HTTP_ERROR",
        error_message="HTTP 403",
        retryable=True,
        job_id=job_id,
        tid=575256,
        payload={"http_status": 403, "transport": "curl_cffi", "remote_identity": "1640438"},
    )

    resp = client.get(
        "/api/logs",
        params={
            "job_id": job_id,
            "tid": 575256,
            "event_type": "image.download.result",
            "level": "WARNING",
            "q": "curl_cffi",
            "errors_only": "true",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["entries"][0]["job_id"] == job_id
    assert data["entries"][0]["payload"]["http_status"] == 403
    assert data["applied_filters"]["event_type"] == "image.download.result"


def test_log_buffer_since_normalizes_timezones_and_errors_include_partial():
    from yamibo_mcp.web_fastapi.log_buffer import LogBuffer

    buffer = LogBuffer()
    buffer._buffer.extend([
        {"ts": "2026-08-22T14:00:00Z", "level": "INFO", "result": "success", "status": "ok"},
        {"ts": "2026-08-22T16:00:00Z", "level": "WARNING", "result": "partial", "status": "partial"},
    ])

    entries = buffer.get_recent(since_ts="2026-08-22T23:00:00+08:00", errors_only=True)

    assert entries == [
        {"ts": "2026-08-22T16:00:00Z", "level": "WARNING", "result": "partial", "status": "partial"}
    ]


def test_chat_context_returns_runtime_payload(client):
    resp = client.get("/api/chat/context")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["transport"] in {"hermes_runs", "hermes_http", "unavailable"}
    assert "mode" in payload


def test_chat_transport_is_probed_during_app_startup(test_settings, monkeypatch):
    from fastapi.testclient import TestClient
    from yamibo_mcp.services.web_chat import ChatService
    from yamibo_mcp.web_fastapi.app import create_app

    calls = []

    def startup_probe(self, **kwargs):
        calls.append(kwargs)
        return {"ready": False}

    monkeypatch.setattr(ChatService, "context", startup_probe)
    with TestClient(create_app(test_settings)) as live_client:
        assert live_client.get("/api/health").status_code == 200
    assert calls == [{"force": True}]


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


def test_job_events_encode_postgres_datetimes(client, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from yamibo_mcp.web_fastapi.routers.jobs import JobEventsRepository

    stamp = datetime(2026, 9, 12, 6, 30, tzinfo=timezone.utc)
    event = SimpleNamespace(event_id=7, job_id='sample', event_type='stage',
                            status='running', stage='fetching',
                            payload={'observed_at': stamp}, created_at=stamp)
    monkeypatch.setattr(JobEventsRepository, 'list', lambda *a, **kw: [event])
    response = client.get('/api/jobs/sample/events')
    assert response.status_code == 200
    assert response.json()[0]['created_at'] == stamp.isoformat()
    assert response.json()[0]['payload']['observed_at'] == stamp.isoformat()
    assert response.headers['X-Next-Event-ID'] == '7'
