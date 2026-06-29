from __future__ import annotations

import io
import json
import sqlite3

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.yamibo.anti_bot import activate_remote_access_pause
from yamibo_mcp.web.routes.dashboard import handle_dashboard


class _CaptureHandler:
    def __init__(self):
        self.command = "GET"
        self.headers = {}
        self.status = None
        self.sent_headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass


def test_dashboard_exposes_live_sync_status_for_active_jobs(db):
    jobs_repo = JobsRepository(db)
    jobs_repo.create("sync_thread", tid=521519)
    jobs_repo.create("noop")

    handler = _CaptureHandler()
    handle_dashboard(handler, db, {"limit": ["10"]})

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["live_thread_statuses"][str(521519)] == "queued"


def test_dashboard_ignores_completed_sync_jobs(db):
    jobs_repo = JobsRepository(db)
    done = jobs_repo.create("sync_thread", tid=540745)
    jobs_repo.succeed(done.job_id, artifacts={"tid": 540745})

    handler = _CaptureHandler()
    handle_dashboard(handler, db, {"limit": ["10"]})

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert str(540745) not in payload["live_thread_statuses"]


def test_dashboard_exposes_remote_access_pause_state(tmp_path):
    db = sqlite3.connect(tmp_path / "dashboard.db")
    db.row_factory = sqlite3.Row
    migrate(db)
    activate_remote_access_pause(
        db,
        source="test",
        message="blocked",
        context={"status_code": 444},
    )
    try:
        handler = _CaptureHandler()
        handle_dashboard(handler, db, {"limit": ["10"]})

        payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert payload["remote_access_pause"]["active"] is True
        assert payload["remote_access_pause"]["source"] == "test"
    finally:
        db.close()
