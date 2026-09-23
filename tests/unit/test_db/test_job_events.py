from __future__ import annotations

import pytest

from yamibo_mcp.db.repositories.job_events import JobEventsRepository, _event_from_row
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobStatus


class TestCreateAppendsEvent:
    def test_create_appends_job_created_event(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        # Act
        job = jobs_repo.create("sync_thread", tid=100)
        # Assert
        events = events_repo.list(job_id=job.job_id)
        assert len(events) == 1
        assert events[0].event_type == "job.created"
        assert events[0].status == JobStatus.QUEUED.value
        assert events[0].job_id == job.job_id


def test_list_latest_events_and_descending_cursor(db):
    job = JobsRepository(db).create("noop")
    repo = JobEventsRepository(db)
    for index in range(4):
        repo.append(job_id=job.job_id, event_type=f"sample.{index}")

    oldest = repo.list(job_id=job.job_id, limit=2)
    latest = repo.list(job_id=job.job_id, limit=2, order="desc")
    previous = repo.list(
        job_id=job.job_id,
        since_event_id=latest[-1].event_id,
        limit=2,
        order="desc",
    )

    assert [event.event_id for event in oldest] == sorted(event.event_id for event in oldest)
    assert [event.event_type for event in latest] == ["sample.3", "sample.2"]
    assert [event.event_type for event in previous] == ["sample.1", "sample.0"]


def test_event_from_row_accepts_postgres_jsonb_dict():
    event = _event_from_row(
        {
            "event_id": 1,
            "job_id": "job_1",
            "event_type": "job.created",
            "status": "queued",
            "stage": None,
            "payload_json": {"worker_id": "worker-1"},
            "created_at": "2026-07-11T00:00:00+00:00",
        }
    )

    assert event.payload == {"worker_id": "worker-1"}


class TestUpdateStageAppendsEvent:
    def test_acquire_appends_started_event(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        # Act
        jobs_repo.acquire(job.job_id, "worker-1", 300)
        # Assert
        events = events_repo.list(job_id=job.job_id)
        started = [e for e in events if e.event_type == "job.started"]
        assert len(started) == 1
        assert started[0].status == JobStatus.RUNNING.value
        assert started[0].payload["worker_id"] == "worker-1"

    def test_update_stage_appends_progressed_event(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        jobs_repo.acquire(job.job_id, "w", 300)
        # Act
        jobs_repo.update_stage(job.job_id, "downloading", progress_current=3, progress_total=10)
        # Assert
        events = events_repo.list(job_id=job.job_id)
        progressed = [e for e in events if e.event_type == "job.progressed"]
        assert len(progressed) == 1
        assert progressed[0].stage == "downloading"
        assert progressed[0].payload["progress_current"] == 3
        assert progressed[0].payload["progress_total"] == 10

    def test_update_stage_partial_progress_payload(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        # Act
        jobs_repo.update_stage(job.job_id, "parsing", progress_current=5)
        # Assert
        events = events_repo.list(job_id=job.job_id)
        progressed = [e for e in events if e.event_type == "job.progressed"]
        assert len(progressed) == 1
        assert progressed[0].payload["progress_current"] == 5
        assert progressed[0].payload["progress_total"] is None


class TestSucceedAppendsEvent:
    def test_succeed_appends_succeeded_event_with_artifacts(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        jobs_repo.acquire(job.job_id, "w", 300)
        artifacts = {"zip": "/path/to.zip", "tid": 42}
        # Act
        jobs_repo.succeed(job.job_id, artifacts=artifacts)
        # Assert
        events = events_repo.list(job_id=job.job_id)
        succeeded = [e for e in events if e.event_type == "job.succeeded"]
        assert len(succeeded) == 1
        assert succeeded[0].status == JobStatus.SUCCEEDED.value
        assert succeeded[0].payload["artifacts"] == artifacts


class TestFailAppendsEvent:
    def test_fail_appends_failed_event_with_error(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        # Act
        jobs_repo.fail(job.job_id, "HTTP_403", "Forbidden")
        # Assert
        events = events_repo.list(job_id=job.job_id)
        failed = [e for e in events if e.event_type == "job.failed"]
        assert len(failed) == 1
        assert failed[0].status == JobStatus.FAILED.value
        assert failed[0].payload["error_code"] == "HTTP_403"
        assert failed[0].payload["error_message"] == "Forbidden"

    def test_fail_appends_failed_event_with_artifacts(self, db):
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")

        jobs_repo.fail(job.job_id, "RemoteFetchError", "boom", artifacts={"failure_context": {"attempts": 3}})

        events = events_repo.list(job_id=job.job_id)
        failed = [e for e in events if e.event_type == "job.failed"]
        assert len(failed) == 1
        assert failed[0].payload["artifacts"]["failure_context"]["attempts"] == 3


class TestRetryLaterAppendsEvent:
    def test_retry_later_appends_retrying_event(self, db):
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("sync_thread", tid=42)
        jobs_repo.acquire(job.job_id, "worker-1", 300)

        jobs_repo.retry_later(
            job.job_id,
            error_code="RemoteFetchError",
            error_message="temporary disconnect",
            artifacts={"failure_context": {"remote_fetch": {"retryable": True}}},
        )

        events = events_repo.list(job_id=job.job_id)
        retrying = [e for e in events if e.event_type == "job.retrying"]
        assert len(retrying) == 1
        assert retrying[0].status == JobStatus.RETRYING.value
        assert retrying[0].payload["retry_count"] == 1


class TestPartialAppendsEvent:
    def test_partial_appends_partial_event_with_artifacts(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("sync_thread", tid=42)
        jobs_repo.acquire(job.job_id, "w", 300)
        artifacts = {"tid": 42, "archive_status": "partial"}
        # Act
        jobs_repo.partial(job.job_id, artifacts=artifacts)
        # Assert
        events = events_repo.list(job_id=job.job_id)
        partial = [e for e in events if e.event_type == "job.partial"]
        assert len(partial) == 1
        assert partial[0].status == JobStatus.PARTIAL.value
        assert partial[0].payload["artifacts"] == artifacts


class TestInterruptedAppendsEvent:
    def test_mark_expired_running_interrupted_appends_event(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("sync_thread", tid=55)
        jobs_repo.acquire(job.job_id, "worker-1", 300)
        db.execute(
            "UPDATE jobs SET lease_until = '2000-01-01T00:00:00+00:00' WHERE job_id = ?",
            (job.job_id,),
        )
        db.commit()
        # Act
        affected = jobs_repo.mark_expired_running_interrupted()
        # Assert
        assert affected == 1
        events = events_repo.list(job_id=job.job_id)
        interrupted = [e for e in events if e.event_type == "job.interrupted"]
        assert len(interrupted) == 1
        assert interrupted[0].status == JobStatus.INTERRUPTED.value
        assert interrupted[0].payload["error_code"] == "LEASE_LOST"
        assert interrupted[0].payload["retry_count"] == 1


class TestEventAppendFailureDoesNotRollback:
    def test_succeed_succeeds_even_if_event_append_fails(self, db, monkeypatch):
        # Arrange
        jobs_repo = JobsRepository(db)
        job = jobs_repo.create("noop")
        jobs_repo.acquire(job.job_id, "w", 300)
        # Patch JobEventsRepository.append to raise
        from yamibo_mcp.db.repositories import job_events
        def _failing_append(self, **kwargs):
            raise RuntimeError("event append failed")
        monkeypatch.setattr(job_events.JobEventsRepository, "append", _failing_append)
        # Act
        jobs_repo.succeed(job.job_id, artifacts={"key": "val"})
        # Assert — job state should still be succeeded
        done = jobs_repo.get(job.job_id)
        assert done.status == JobStatus.SUCCEEDED
        assert done.artifacts == {"key": "val"}

    def test_fail_succeeds_even_if_event_append_fails(self, db, monkeypatch):
        # Arrange
        jobs_repo = JobsRepository(db)
        job = jobs_repo.create("noop")
        from yamibo_mcp.db.repositories import job_events
        def _failing_append(self, **kwargs):
            raise RuntimeError("event append failed")
        monkeypatch.setattr(job_events.JobEventsRepository, "append", _failing_append)
        # Act
        jobs_repo.fail(job.job_id, "ERR", "msg")
        # Assert
        failed = jobs_repo.get(job.job_id)
        assert failed.status == JobStatus.FAILED
        assert failed.error_code == "ERR"

    def test_update_stage_succeeds_even_if_event_append_fails(self, db, monkeypatch):
        # Arrange
        jobs_repo = JobsRepository(db)
        job = jobs_repo.create("noop")
        from yamibo_mcp.db.repositories import job_events
        def _failing_append(self, **kwargs):
            raise RuntimeError("event append failed")
        monkeypatch.setattr(job_events.JobEventsRepository, "append", _failing_append)
        # Act
        jobs_repo.update_stage(job.job_id, "downloading", progress_current=1, progress_total=5)
        # Assert
        updated = jobs_repo.get(job.job_id)
        assert updated.stage == "downloading"
        assert updated.progress_current == 1


class TestJobEventsRepositoryList:
    def test_list_ordered_by_event_id(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        jobs_repo.update_stage(job.job_id, "stage1")
        jobs_repo.update_stage(job.job_id, "stage2")
        # Act
        events = events_repo.list(job_id=job.job_id)
        # Assert
        event_ids = [e.event_id for e in events]
        assert event_ids == sorted(event_ids)
        assert len(events) >= 3  # created + 2 progressed

    def test_list_since_event_id(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        jobs_repo.update_stage(job.job_id, "stage1")
        all_events = events_repo.list(job_id=job.job_id)
        # Act — get events after the first one
        later_events = events_repo.list(job_id=job.job_id, since_event_id=all_events[0].event_id)
        # Assert
        assert len(later_events) == len(all_events) - 1
        assert all(e.event_id > all_events[0].event_id for e in later_events)

    def test_list_respects_limit(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        job = jobs_repo.create("noop")
        for i in range(5):
            jobs_repo.update_stage(job.job_id, f"stage_{i}")
        # Act
        limited = events_repo.list(job_id=job.job_id, limit=3)
        # Assert
        assert len(limited) == 3

    def test_list_returns_empty_for_unknown_job(self, db):
        events_repo = JobEventsRepository(db)
        events = events_repo.list(job_id="nonexistent")
        assert events == []

    def test_list_without_filters_returns_all(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        j1 = jobs_repo.create("noop")
        j2 = jobs_repo.create("noop")
        # Act
        events = events_repo.list()
        # Assert
        job_ids = {e.job_id for e in events}
        assert j1.job_id in job_ids
        assert j2.job_id in job_ids


class TestFullLifecycleEvents:
    def test_full_lifecycle_has_all_events(self, db):
        # Arrange
        jobs_repo = JobsRepository(db)
        events_repo = JobEventsRepository(db)
        # Act
        job = jobs_repo.create("sync_thread", tid=42)
        jobs_repo.acquire(job.job_id, "w", 300)
        jobs_repo.update_stage(job.job_id, "parse", progress_current=1, progress_total=6)
        jobs_repo.update_stage(job.job_id, "download", progress_current=2, progress_total=6)
        jobs_repo.succeed(job.job_id, artifacts={"tid": 42})
        # Assert
        events = events_repo.list(job_id=job.job_id)
        types = [e.event_type for e in events]
        assert types[0] == "job.created"
        assert "job.started" in types
        assert "job.progressed" in types
        assert types[-1] == "job.succeeded"
        assert len(events) >= 5
