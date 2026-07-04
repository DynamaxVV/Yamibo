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
    assert calls["error_code"] == "REMOTE_CONNECTION_ERROR"
    assert calls["artifacts"]["failure_context"]["exception_type"] == "RemoteFetchError"


def test_run_once_does_not_acquire_jobs_when_jobs_disabled(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        db_path=tmp_path / "test.db",
        worker_id="daemon-test",
        jobs_enabled=False,
        worker_poll_seconds=0.0,
        worker_lease_seconds=300,
    )
    runner = DaemonRunner(settings, worker_id="daemon-test-1")

    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.acquire_next",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("disabled daemon should not acquire jobs")),
    )

    result = runner.run_once()

    assert result.processed == 0


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
    assert calls["error_code"] == "INTERNAL_ERROR"
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
    assert calls["error_code"] == "REMOTE_THREAD_PERMISSION_REQUIRED"
    assert "requires higher read permission" in caplog.text


def test_run_once_pauses_on_http_444(tmp_path, monkeypatch):
    """HTTP 444 single occurrence fails the job; 3 consecutive 444s escalate to global pause."""
    import yamibo_mcp.daemon.runner as runner_mod

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
    pause_calls: list[dict] = []
    fail_calls: list[str] = []

    def _handler(repo, current_job, worker_id, lease_seconds, handler_settings):
        raise RemoteFetchError(
            "HTTP Error 444: ...",
            details={"url": "https://bbs.yamibo.com/forum.php", "status_code": 444, "retryable": False},
        )

    monkeypatch.setattr("yamibo_mcp.daemon.runner.get_handler", lambda _job: _handler)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.recover_expired_jobs", lambda repo: None)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.JobsRepository.acquire_next", lambda self, worker_id, lease_seconds: job)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.activate_remote_access_pause",
        lambda conn, source, message, context=None: pause_calls.append({"source": source, "message": message}),
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.finalize_pause",
        lambda self, job_id: None,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.fail",
        lambda self, job_id, error_code, error_message, artifacts=None: fail_calls.append(job_id),
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.retry_later",
        lambda self, job_id, *, error_code, error_message, artifacts=None: None,
    )
    monkeypatch.setattr("yamibo_mcp.daemon.runner.clear_proxy_cache", lambda: 0)

    # Clear 444 counter before test
    with runner_mod._444_LOCK:
        runner_mod._444_EVENTS.clear()

    # First 444 — should retry job with different proxy, NOT fail or pause
    result1 = runner.run_once()
    assert result1.processed == 1
    assert len(pause_calls) == 0  # no global pause
    assert len(fail_calls) == 0   # retry_later instead of fail

    # Second 444
    result2 = runner.run_once()
    assert len(pause_calls) == 0

    # Third 444 — should escalate to global pause
    result3 = runner.run_once()
    assert result3.processed == 1
    assert len(pause_calls) == 1
    assert "3 times within" in pause_calls[0]["message"]
    assert len(fail_calls) == 0  # all three retried, third also paused globally


def test_run_once_detects_http_444_via_curl_error_92(tmp_path, monkeypatch):
    """curl error 92 (HTTP/2 PROTOCOL_ERROR) is the curl_cffi manifestation of HTTP 444."""
    import yamibo_mcp.daemon.runner as runner_mod

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
    retry_calls: list[str] = []
    fail_calls: list[str] = []

    def _handler(repo, current_job, worker_id, lease_seconds, handler_settings):
        raise RemoteFetchError(
            "failed to fetch https://bbs.yamibo.com/forum.php?mod=viewthread&tid=19787&page=1 "
            "after 3 attempt(s) with timeout=30.0s: "
            "Failed to perform, curl: (92) HTTP/2 stream 5 was not closed cleanly: "
            "PROTOCOL_ERROR (err 1)",
            details={
                "url": "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=19787&page=1",
                "attempts": 3,
                "timeout_seconds": 30.0,
                "last_error_type": "RequestsError",
                "last_error_message": (
                    "Failed to perform, curl: (92) HTTP/2 stream 5 was not closed cleanly: "
                    "PROTOCOL_ERROR (err 1)"
                ),
            },
        )

    monkeypatch.setattr("yamibo_mcp.daemon.runner.get_handler", lambda _job: _handler)
    monkeypatch.setattr("yamibo_mcp.daemon.runner.recover_expired_jobs", lambda repo: None)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.acquire_next",
        lambda self, worker_id, lease_seconds: job,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.retry_later",
        lambda self, job_id, *, error_code, error_message, artifacts=None: retry_calls.append(job_id),
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.runner.JobsRepository.fail",
        lambda self, job_id, error_code, error_message, artifacts=None: fail_calls.append(job_id),
    )
    monkeypatch.setattr("yamibo_mcp.daemon.runner.clear_proxy_cache", lambda: 0)

    # Clear 444 counter before test
    with runner_mod._444_LOCK:
        runner_mod._444_EVENTS.clear()

    result = runner.run_once()
    assert result.processed == 1
    # Should be detected as HTTP 444 → retry_later, NOT fail
    assert len(retry_calls) == 1
    assert len(fail_calls) == 0
