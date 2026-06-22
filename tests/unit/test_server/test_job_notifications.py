from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch

from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.job_notifications import JobResourceNotifier
from yamibo_mcp.server.resource_uris import job_events_uri, job_status_uri


def _fake_settings(tmp_path: Path):
    class Settings:
        pass

    settings = Settings()
    settings.db_path = tmp_path / "test.db"
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.novel_txt_export_dir = tmp_path / "novel_exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.novel_txt_export_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _open_db(settings):
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


class _FakeSession:
    def __init__(self) -> None:
        self.updated_uris: list[str] = []

    async def send_resource_updated(self, uri: str) -> None:
        self.updated_uris.append(uri)


def test_job_resource_notifier_emits_status_and_events_updates(tmp_path):
    settings = _fake_settings(tmp_path)
    conn = _open_db(settings)
    repo = JobsRepository(conn)
    job = repo.create("sync_thread", tid=100, payload={"tid": 100})
    conn.commit()
    conn.close()

    session = _FakeSession()
    notifier = JobResourceNotifier(poll_interval_seconds=0.01)

    async def scenario() -> None:
        with patch("yamibo_mcp.server.job_notifications.load_settings", return_value=settings):
            await notifier.subscribe(job_status_uri(job.job_id), session)
            await notifier.subscribe(job_events_uri(job.job_id), session)

            conn = _open_db(settings)
            JobsRepository(conn).succeed(job.job_id, {"tid": 100})
            conn.commit()
            conn.close()

            await notifier.poll_once()

    asyncio.run(scenario())

    assert job_status_uri(job.job_id) in session.updated_uris
    assert job_events_uri(job.job_id) in session.updated_uris


def test_job_resource_notifier_rejects_non_job_uri(tmp_path):
    settings = _fake_settings(tmp_path)
    session = _FakeSession()
    notifier = JobResourceNotifier()

    async def scenario() -> None:
        with patch("yamibo_mcp.server.job_notifications.load_settings", return_value=settings):
            try:
                await notifier.subscribe("yamibo://threads/1/context", session)
                assert False, "expected invalid URI subscription to fail"
            except ValueError as exc:
                assert "unsupported subscribable resource uri" in str(exc)

    asyncio.run(scenario())
