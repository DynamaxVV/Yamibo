import sqlite3

import pytest

import yamibo_mcp.db.repositories.jobs as jobs_module
from yamibo_mcp.db.repositories.jobs import JobsRepository, _loads
from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.domain.models import Job
from yamibo_mcp.errors import JobNotFound, LeaseNotAcquired


def test_locked_write_retry_retries_database_locked(monkeypatch, db):
    repo = JobsRepository(db)
    calls = {"count": 0}
    monkeypatch.setattr(jobs_module, "_LOCK_RETRY_DELAYS_SECONDS", (0,))

    def operation():
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert repo._with_locked_retry(operation) == "ok"
    assert calls["count"] == 2


def test_jobs_json_loader_accepts_postgres_jsonb_dict():
    assert _loads({"tid": 42}) == {"tid": 42}


class TestCreateAndGet:
    def test_create_returns_job_with_queued_status(self, db):
        # Arrange
        repo = JobsRepository(db)
        # Act
        job = repo.create("sync_thread", tid=123)
        # Assert
        assert job.job_type == "sync_thread"
        assert job.tid == 123
        assert job.status == JobStatus.QUEUED

    def test_get_round_trip(self, db):
        # Arrange
        repo = JobsRepository(db)
        created = repo.create("export_thread", tid=456, payload={"key": "value"})
        # Act
        fetched = repo.get(created.job_id)
        # Assert
        assert fetched.job_id == created.job_id
        assert fetched.payload == {"key": "value"}

    def test_create_reuses_exact_queued_duplicate(self, db):
        # Arrange
        repo = JobsRepository(db)
        first = repo.create("sync_thread", tid=123, payload={"force": False})

        # Act
        second = repo.create("sync_thread", tid=123, payload={"force": False})

        # Assert
        assert second.job_id == first.job_id
        queued = repo.list(status=JobStatus.QUEUED)
        assert len(queued) == 1
        assert queued[0].job_id == first.job_id

    def test_create_default_max_retries(self, db):
        # Arrange
        repo = JobsRepository(db)
        # Act
        job = repo.create("noop")
        # Assert
        assert job.max_retries == 3
        assert job.resumable is True

    @pytest.mark.parametrize(
        "kwargs,expected_tid,expected_max_retries,expected_resumable",
        [
            pytest.param({"tid": 10, "max_retries": 1}, 10, 1, True, id="custom_retries"),
            pytest.param({"resumable": False}, None, 3, False, id="not_resumable"),
            pytest.param({"parent_job_id": "parent_abc"}, None, 3, True, id="with_parent"),
        ],
    )
    def test_create_with_options(self, db, kwargs, expected_tid, expected_max_retries, expected_resumable):
        # Arrange
        repo = JobsRepository(db)
        # Act
        job = repo.create("noop", **kwargs)
        # Assert
        assert job.tid == expected_tid
        assert job.max_retries == expected_max_retries
        assert job.resumable == expected_resumable


class TestGetNotFound:
    def test_get_nonexistent_raises_job_not_found(self, db):
        # Arrange
        repo = JobsRepository(db)
        # Act & Assert
        with pytest.raises(JobNotFound):
            repo.get("nonexistent_id")


class TestList:
    def test_list_returns_created_jobs(self, db):
        # Arrange
        repo = JobsRepository(db)
        repo.create("noop")
        repo.create("sync_thread")
        # Act
        jobs = repo.list()
        # Assert
        assert len(jobs) == 2
        types = {j.job_type for j in jobs}
        assert types == {"noop", "sync_thread"}

    def test_list_respects_limit(self, db):
        # Arrange
        repo = JobsRepository(db)
        for _ in range(5):
            repo.create("noop")
        # Act
        jobs = repo.list(limit=3)
        # Assert
        assert len(jobs) == 3

    def test_list_respects_offset(self, db):
        repo = JobsRepository(db)
        first = repo.create("noop")
        second = repo.create("noop")
        third = repo.create("noop")
        db.execute("UPDATE jobs SET created_at = ? WHERE job_id = ?", ("2026-01-01T08:00:00+00:00", first.job_id))
        db.execute("UPDATE jobs SET created_at = ? WHERE job_id = ?", ("2026-01-01T09:00:00+00:00", second.job_id))
        db.execute("UPDATE jobs SET created_at = ? WHERE job_id = ?", ("2026-01-01T10:00:00+00:00", third.job_id))
        db.commit()

        jobs = repo.list(limit=2, offset=1)

        assert [job.job_id for job in jobs] == [second.job_id, third.job_id]

    def test_list_filters_by_status(self, db):
        # Arrange
        repo = JobsRepository(db)
        j1 = repo.create("noop")
        j2 = repo.create("noop")
        repo.succeed(j1.job_id)
        # Act
        queued = repo.list(status=JobStatus.QUEUED)
        succeeded = repo.list(status=JobStatus.SUCCEEDED)
        # Assert
        assert len(queued) == 1
        assert queued[0].job_id == j2.job_id
        assert len(succeeded) == 1
        assert succeeded[0].job_id == j1.job_id

    def test_list_orders_active_then_queued_then_terminal_by_start_time(self, db):
        # Arrange
        repo = JobsRepository(db)
        active_late = repo.create("noop")
        active_early = repo.create("noop")
        queued = repo.create("noop")
        terminal = repo.create("noop")
        db.execute(
            "UPDATE jobs SET status = ?, created_at = ? WHERE job_id = ?",
            ("running", "2026-01-01T10:00:00+00:00", active_late.job_id),
        )
        db.execute(
            "UPDATE jobs SET status = ?, created_at = ? WHERE job_id = ?",
            ("retrying", "2026-01-01T09:00:00+00:00", active_early.job_id),
        )
        db.execute(
            "UPDATE jobs SET status = ?, created_at = ? WHERE job_id = ?",
            ("queued", "2026-01-01T08:00:00+00:00", queued.job_id),
        )
        db.execute(
            "UPDATE jobs SET status = ?, created_at = ? WHERE job_id = ?",
            ("succeeded", "2026-01-01T07:00:00+00:00", terminal.job_id),
        )
        db.commit()

        # Act
        jobs = repo.list()

        # Assert
        assert [j.job_id for j in jobs] == [active_early.job_id, active_late.job_id, queued.job_id, terminal.job_id]

    def test_list_empty(self, db):
        # Arrange
        repo = JobsRepository(db)
        # Act
        jobs = repo.list()
        # Assert
        assert jobs == []

    def test_count_returns_filtered_total(self, db):
        repo = JobsRepository(db)
        queued = repo.create("noop")
        done = repo.create("noop")
        repo.succeed(done.job_id)

        assert repo.count_filtered() == 2
        assert repo.count_filtered(status=JobStatus.QUEUED) == 1
        assert repo.count_filtered(status=JobStatus.SUCCEEDED) == 1


class TestFindLiveJobForThread:
    def test_returns_queued_job_for_same_tid_and_type(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=123)
        # Act
        found = repo.find_live_job_for_thread(job_type="sync_thread", tid=123)
        # Assert
        assert found is not None
        assert found.job_id == job.job_id

    def test_prefers_most_recent_live_job(self, db):
        # Arrange
        repo = JobsRepository(db)
        older = repo.create("sync_thread", tid=123)
        newer = repo.create("sync_thread", tid=123)
        # Act
        found = repo.find_live_job_for_thread(job_type="sync_thread", tid=123)
        # Assert
        assert found is not None
        assert found.job_id == newer.job_id

    def test_ignores_terminal_jobs(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=123)
        repo.succeed(job.job_id)
        # Act
        found = repo.find_live_job_for_thread(job_type="sync_thread", tid=123)
        # Assert
        assert found is None

    def test_scopes_by_job_type(self, db):
        # Arrange
        repo = JobsRepository(db)
        repo.create("sync_thread", tid=123)
        # Act
        found = repo.find_live_job_for_thread(job_type="export_thread", tid=123)
        # Assert
        assert found is None


class TestAcquire:
    def test_acquire_changes_status_to_running(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        # Act
        acquired = repo.acquire(job.job_id, "worker-1", 300)
        # Assert
        assert acquired.status == JobStatus.RUNNING
        assert acquired.worker_id == "worker-1"
        assert acquired.lease_until is not None

    def test_acquire_already_acquired_raises_lease_not_acquired(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "worker-1", 300)
        # Act & Assert
        with pytest.raises(LeaseNotAcquired):
            repo.acquire(job.job_id, "worker-2", 300)

    def test_acquire_nonexistent_raises_lease_not_acquired(self, db):
        # Arrange
        repo = JobsRepository(db)
        # Act & Assert
        with pytest.raises(LeaseNotAcquired):
            repo.acquire("no_such_id", "worker-1", 300)


class TestAcquireNext:
    def test_acquire_next_picks_oldest_queued(self, db):
        # Arrange
        repo = JobsRepository(db)
        first = repo.create("noop")
        second = repo.create("noop")
        db.execute(
            "UPDATE jobs SET created_at = ? WHERE job_id = ?",
            ("2026-01-01T09:00:00+00:00", first.job_id),
        )
        db.execute(
            "UPDATE jobs SET created_at = ? WHERE job_id = ?",
            ("2026-01-01T09:00:01+00:00", second.job_id),
        )
        db.commit()
        # Act
        acquired = repo.acquire_next("worker-1", 300)
        # Assert
        assert acquired.job_id == first.job_id
        remaining = repo.list(status=JobStatus.QUEUED)
        assert len(remaining) == 1
        assert remaining[0].job_id == second.job_id

    def test_acquire_next_returns_none_when_no_queued(self, db):
        # Arrange
        repo = JobsRepository(db)
        # Act
        result = repo.acquire_next("worker-1", 300)
        # Assert
        assert result is None

    def test_acquire_next_picks_retrying_job(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.fail(job.job_id, "err", "msg")
        # Manually set to retrying
        db.execute("UPDATE jobs SET status = ? WHERE job_id = ?", (JobStatus.RETRYING.value, job.job_id))
        db.commit()
        # Act
        acquired = repo.acquire_next("worker-1", 300)
        # Assert
        assert acquired is not None
        assert acquired.job_id == job.job_id

    def test_acquire_next_prefers_queued_over_interrupted(self, db):
        # Arrange
        repo = JobsRepository(db)
        interrupted = repo.create("noop")
        queued = repo.create("noop")
        db.execute("UPDATE jobs SET status = ?, lease_until = NULL WHERE job_id = ?", (JobStatus.INTERRUPTED.value, interrupted.job_id))
        db.commit()
        # Act
        acquired = repo.acquire_next("worker-1", 300)
        # Assert
        assert acquired is not None
        assert acquired.job_id == queued.job_id


class TestHeartbeat:
    def test_heartbeat_extends_lease(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "worker-1", 60)
        # Act
        repo.heartbeat(job.job_id, "worker-1", 600)
        # Assert
        refreshed = repo.get(job.job_id)
        assert refreshed.heartbeat_at is not None

    def test_heartbeat_ignores_wrong_worker(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "worker-1", 60)
        # Act — heartbeat with wrong worker_id should be no-op (no matching row)
        repo.heartbeat(job.job_id, "worker-999", 600)
        # Assert — no error raised, job unchanged
        refreshed = repo.get(job.job_id)
        assert refreshed.worker_id == "worker-1"


class TestSucceed:
    def test_succeed_changes_status(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "w", 300)
        # Act
        repo.succeed(job.job_id, artifacts={"zip": "/path/to.zip"})
        # Assert
        done = repo.get(job.job_id)
        assert done.status == JobStatus.SUCCEEDED
        assert done.artifacts == {"zip": "/path/to.zip"}
        assert done.finished_at is not None


class TestFail:
    def test_fail_changes_status_with_error_fields(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        # Act
        repo.fail(job.job_id, "HTTP_403", "Forbidden")
        # Assert
        failed = repo.get(job.job_id)
        assert failed.status == JobStatus.FAILED
        assert failed.error_code == "HTTP_403"
        assert failed.error_message == "Forbidden"
        assert failed.finished_at is not None

    def test_fail_merges_artifacts(self, db):
        repo = JobsRepository(db)
        job = repo.create("noop")
        db.execute("UPDATE jobs SET artifacts_json = ? WHERE job_id = ?", ('{"tid": 42}', job.job_id))
        db.commit()

        repo.fail(job.job_id, "RemoteFetchError", "boom", artifacts={"failure_context": {"url": "https://bbs.yamibo.com"}})

        failed = repo.get(job.job_id)
        assert failed.artifacts["tid"] == 42
        assert failed.artifacts["failure_context"]["url"] == "https://bbs.yamibo.com"


class TestRetryLater:
    def test_retry_later_moves_running_job_to_retrying(self, db):
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=42)
        repo.acquire(job.job_id, "worker-1", 300)

        ok = repo.retry_later(
            job.job_id,
            error_code="RemoteFetchError",
            error_message="temporary disconnect",
            artifacts={"failure_context": {"remote_fetch": {"retryable": True}}},
        )

        assert ok is True
        retried = repo.get(job.job_id)
        assert retried.status == JobStatus.RETRYING
        assert retried.retry_count == 1
        assert retried.worker_id is None
        assert retried.artifacts["failure_context"]["remote_fetch"]["retryable"] is True

    def test_retry_later_respects_max_retries(self, db):
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=42, max_retries=1)
        repo.acquire(job.job_id, "worker-1", 300)

        assert repo.retry_later(job.job_id, error_code="RemoteFetchError", error_message="once") is True

        db.execute(
            "UPDATE jobs SET status = ?, worker_id = ?, lease_until = ? WHERE job_id = ?",
            (JobStatus.RUNNING.value, "worker-1", "2099-01-01T00:00:00+00:00", job.job_id),
        )
        db.commit()

        assert repo.retry_later(job.job_id, error_code="RemoteFetchError", error_message="twice") is False


class TestRerun:
    def test_rerun_moves_partial_job_to_superseded_and_creates_queued_copy(self, db):
        repo = JobsRepository(db)
        job = repo.create("rag_index", tid=42, payload={"tid": 42, "force": False})
        repo.partial(job.job_id, artifacts={"warning": "embedding failed: 'data'"})

        next_job = repo.rerun(job.job_id)

        source = repo.get(job.job_id)
        assert source.status == JobStatus.SUPERSEDED
        assert source.finished_at is not None
        assert source.artifacts == {"warning": "embedding failed: 'data'"}
        assert next_job.status == JobStatus.QUEUED
        assert next_job.job_type == "rag_index"
        assert next_job.tid == 42
        assert next_job.payload == {"tid": 42, "force": False}
        parent_row = db.execute("SELECT parent_job_id FROM jobs WHERE job_id = ?", (next_job.job_id,)).fetchone()
        assert parent_row["parent_job_id"] == job.job_id

    def test_rerun_moves_failed_job_to_superseded_and_keeps_payload(self, db):
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=99, payload={"tid": 99})
        repo.fail(job.job_id, "HTTP_500", "boom")

        next_job = repo.rerun(job.job_id)

        source = repo.get(job.job_id)
        assert source.status == JobStatus.SUPERSEDED
        assert source.error_code == "HTTP_500"
        assert source.error_message == "boom"
        assert next_job.status == JobStatus.QUEUED
        parent_row = db.execute("SELECT parent_job_id FROM jobs WHERE job_id = ?", (next_job.job_id,)).fetchone()
        assert parent_row["parent_job_id"] == job.job_id

    def test_rerun_moves_interrupted_job_to_superseded_and_keeps_payload(self, db):
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=100, payload={"tid": 100})
        db.execute(
            "UPDATE jobs SET status = ?, error_code = ?, error_message = ? WHERE job_id = ?",
            (JobStatus.INTERRUPTED.value, "worker_lost", "lease expired", job.job_id),
        )
        db.commit()

        next_job = repo.rerun(job.job_id)

        source = repo.get(job.job_id)
        assert source.status == JobStatus.SUPERSEDED
        assert source.error_code == "worker_lost"
        assert source.error_message == "lease expired"
        assert next_job.status == JobStatus.QUEUED
        assert next_job.payload == {"tid": 100}


class TestUpdateStage:
    def test_update_stage_sets_stage_and_progress(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        # Act
        repo.update_stage(job.job_id, "downloading", progress_current=3, progress_total=10)
        # Assert
        updated = repo.get(job.job_id)
        assert updated.stage == "downloading"
        assert updated.progress_current == 3
        assert updated.progress_total == 10

    def test_update_stage_partial_progress(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        # Act
        repo.update_stage(job.job_id, "parsing", progress_current=5)
        # Assert
        updated = repo.get(job.job_id)
        assert updated.stage == "parsing"
        assert updated.progress_current == 5
        assert updated.progress_total is None


class TestMarkExpiredRunningInterrupted:
    def test_marks_expired_running_jobs(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "w", 300)
        # Force lease_until to the past
        db.execute(
            "UPDATE jobs SET lease_until = '2000-01-01T00:00:00' WHERE job_id = ?",
            (job.job_id,),
        )
        db.commit()
        # Act
        count = repo.mark_expired_running_interrupted()
        # Assert
        assert count == 1
        updated = repo.get(job.job_id)
        assert updated.status == JobStatus.INTERRUPTED

    def test_does_not_mark_non_expired_jobs(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "w", 300)
        # Act
        count = repo.mark_expired_running_interrupted()
        # Assert
        assert count == 0
        updated = repo.get(job.job_id)
        assert updated.status == JobStatus.RUNNING

    def test_returns_zero_when_no_running_jobs(self, db):
        # Arrange
        repo = JobsRepository(db)
        repo.create("noop")
        # Act
        count = repo.mark_expired_running_interrupted()
        # Assert
        assert count == 0


class TestPausedRecovery:
    def test_release_expired_paused_jobs_clears_worker_ownership(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "worker-1", 300)
        repo.pause(job.job_id)
        db.execute(
            "UPDATE jobs SET lease_until = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job.job_id),
        )
        db.commit()

        # Act
        count = repo.release_expired_paused_jobs()

        # Assert
        assert count == 1
        updated = repo.get(job.job_id)
        assert updated.status == JobStatus.PAUSED
        assert updated.worker_id is None
        assert updated.lease_until is None

    def test_resume_releases_expired_paused_job(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "worker-1", 300)
        repo.pause(job.job_id)
        db.execute(
            "UPDATE jobs SET lease_until = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", job.job_id),
        )
        db.commit()

        # Act
        ok = repo.resume(job.job_id)

        # Assert
        assert ok is True
        updated = repo.get(job.job_id)
        assert updated.status == JobStatus.QUEUED
        assert updated.worker_id is None
        assert updated.lease_until is None

    def test_resume_rejects_live_paused_job(self, db):
        # Arrange
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.acquire(job.job_id, "worker-1", 300)
        repo.pause(job.job_id)
        # Act
        ok = repo.resume(job.job_id)

        # Assert
        assert ok is False
        updated = repo.get(job.job_id)
        assert updated.status == JobStatus.PAUSED
