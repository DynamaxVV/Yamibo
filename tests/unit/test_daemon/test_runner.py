from __future__ import annotations

import sqlite3
from threading import Event
from types import SimpleNamespace

from yamibo_mcp.daemon.runner import DaemonRunner
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.errors import RemoteFetchError, ThreadPermissionRequiredError


def test_worker_loop_logs_and_recovers_from_outer_exception(monkeypatch, caplog):
    runner = DaemonRunner(
        SimpleNamespace(
            worker_id="daemon-test",
            worker_poll_seconds=0.0,
        ),
        worker_id="daemon-test-1",
    )
    stop_event = Event()
    calls = {"count": 0}

    def _fake_run_once():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        stop_event.set()
        return SimpleNamespace(processed=0)

    monkeypatch.setattr(runner, "run_once", _fake_run_once)

    with caplog.at_level("INFO"):
        runner._run_worker_loop(stop_event)

    assert calls["count"] == 2
    assert "Worker daemon-test-1 crashed outside job handler loop" in caplog.text
    assert "Worker daemon-test-1 stopped" in caplog.text


def test_run_once_moves_retryable_remote_fetch_error_to_retrying(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    job = JobsRepository(conn).create("sync_thread", tid=42)
    conn.close()

    settings = SimpleNamespace(
        db_path=db_path,
        worker_id="daemon-test",
        worker_poll_seconds=0.0,
        worker_lease_seconds=300,
    )
    runner = DaemonRunner(settings, worker_id="daemon-test-1")
    calls = {}

    def _handler(repo, current_job, worker_id, lease_seconds, handler_settings):
        raise RemoteFetchError(
            "failed to fetch",
            details={"retryable": True, "last_error_type": "RemoteDisconnected"},
        )

    monkeypatch.setattr("yamibo_mcp.daemon.runner.get_handler", lambda _job: _handler)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.recover_expired_jobs", lambda repo: None)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.JobsRepository.acquire_next", lambda self, worker_id, lease_seconds: job)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.retry_later",
        lambda self, job_id, error_code, error_message, artifacts=None: calls.update(
            {"job_id": job_id, "error_code": error_code, "error_message": error_message, "artifacts": artifacts}
        )
        or True,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.fail",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fail() should not be called for retryable remote fetch errors")),
    )

    result = runner.run_once()

    assert result.processed == 1
    assert calls["job_id"] == job.job_id
    assert calls["error_code"] == "RemoteFetchError"
    assert calls["artifacts"]["failure_context"]["exception_type"] == "RemoteFetchError"


def test_run_once_rolls_back_handler_transaction_before_marking_failed(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    job = JobsRepository(conn).create("sync_thread", tid=42)
    conn.close()

    settings = SimpleNamespace(
        db_path=db_path,
        worker_id="daemon-test",
        worker_poll_seconds=0.0,
        worker_lease_seconds=300,
    )
    runner = DaemonRunner(settings, worker_id="daemon-test-1")
    calls = {}

    def _handler(repo, current_job, worker_id, lease_seconds, handler_settings):
        repo.conn.execute("UPDATE jobs SET stage = ? WHERE job_id = ?", ("dirty", current_job.job_id))
        raise RuntimeError("boom")

    monkeypatch.setattr("yamibo_mcp.daemon.runner.get_handler", lambda _job: _handler)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.recover_expired_jobs", lambda repo: None)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.JobsRepository.acquire_next", lambda self, worker_id, lease_seconds: job)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.fail",
        lambda self, job_id, error_code, error_message, artifacts=None: calls.update(
            {"job_id": job_id, "error_code": error_code, "error_message": error_message, "artifacts": artifacts}
        ),
    )

    result = runner.run_once()

    assert result.processed == 1
    assert calls["job_id"] == job.job_id
    assert calls["error_code"] == "RuntimeError"
    assert calls["artifacts"]["failure_context"]["exception_type"] == "RuntimeError"


def test_run_once_logs_permission_error_without_traceback(tmp_path, monkeypatch, caplog):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    job = JobsRepository(conn).create("sync_thread", tid=42)
    conn.close()

    settings = SimpleNamespace(
        db_path=db_path,
        worker_id="daemon-test",
        worker_poll_seconds=0.0,
        worker_lease_seconds=300,
    )
    runner = DaemonRunner(settings, worker_id="daemon-test-1")
    calls = {}

    def _handler(repo, current_job, worker_id, lease_seconds, handler_settings):
        raise ThreadPermissionRequiredError("thread requires read permission above 10 for ...", required_permission=10)

    monkeypatch.setattr("yamibo_mcp.daemon.runner.get_handler", lambda _job: _handler)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.recover_expired_jobs", lambda repo: None)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.JobsRepository.acquire_next", lambda self, worker_id, lease_seconds: job)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.fail",
        lambda self, job_id, error_code, error_message, artifacts=None: calls.update(
            {"job_id": job_id, "error_code": error_code, "error_message": error_message, "artifacts": artifacts}
        ),
    )

    with caplog.at_level("WARNING"):
        result = runner.run_once()

    assert result.processed == 1
    assert calls["job_id"] == job.job_id
    assert calls["error_code"] == "ThreadPermissionRequiredError"
    assert "requires higher read permission" in caplog.text
