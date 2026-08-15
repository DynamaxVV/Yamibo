from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from yamibo_mcp.application.job_queries import get_job_status_payload
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.schemas import job_status_payload as direct_job_status_payload


_JOB_STATUS_KEYS = {
    "job_id", "job_type", "status", "stage", "progress_current", "progress_total",
    "worker_id", "error_code", "error_message", "artifacts", "created_at",
    "updated_at", "finished_at", "is_terminal", "result_ready",
    "running_duration_seconds", "seconds_since_update", "execution_state",
    "diagnostic_summary", "needs_attention",
    "recommended_poll_after_seconds", "resources",
    "retry_count", "max_retries", "next_retry_at", "recovery",
}


def _fake_settings(tmp_path: Path):
    settings = MagicMock()
    settings.db_path = str(tmp_path / "test.db")
    return settings


class TestGetJobStatusPayload:
    def test_returns_same_keys_as_direct_schema_call(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=100, payload={"tid": 100})
        expected = direct_job_status_payload(job)
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db):
            # Act
            result = get_job_status_payload(job.job_id)
        # Assert
        assert result.keys() == expected.keys()
        assert result["job_id"] == job.job_id
        assert result["job_type"] == "sync_thread"
        assert result["status"] == "queued"
        assert result["is_terminal"] is False
        assert result["result_ready"] is False
        assert result["recommended_poll_after_seconds"] == 2

    def test_returns_all_expected_keys(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("noop")
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db):
            # Act
            result = get_job_status_payload(job.job_id)
        # Assert
        assert _JOB_STATUS_KEYS == set(result.keys())

    def test_marks_succeeded_job_as_terminal_and_result_ready(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("noop")
        repo.succeed(job.job_id)
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db):
            # Act
            result = get_job_status_payload(job.job_id)
        # Assert
        assert result["is_terminal"] is True
        assert result["result_ready"] is True
        assert result["recommended_poll_after_seconds"] is None

    def test_marks_superseded_job_as_terminal_but_not_result_ready(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("noop")
        db.execute(
            "UPDATE jobs SET status = 'superseded', finished_at = '2026-06-22T14:03:40+00:00' WHERE job_id = ?",
            (job.job_id,),
        )
        db.commit()
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db), \
             patch("yamibo_mcp.server.schemas.utc_now_iso", return_value="2026-06-22T14:03:40+00:00"):
            result = get_job_status_payload(job.job_id)

        assert result["is_terminal"] is True
        assert result["result_ready"] is False
        assert result["execution_state"] == "terminal"
        assert result["diagnostic_summary"].startswith("Job was superseded")

    def test_marks_old_running_download_job_as_attention_with_diagnostic_summary(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=100, payload={"tid": 100})
        db.execute(
            """
            UPDATE jobs
            SET status = 'running',
                stage = 'download_images',
                progress_current = 4,
                progress_total = 6,
                created_at = '2026-06-22T14:00:00+00:00',
                updated_at = '2026-06-22T14:03:10+00:00'
            WHERE job_id = ?
            """,
            (job.job_id,),
        )
        db.commit()
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db), \
             patch("yamibo_mcp.server.schemas.utc_now_iso", return_value="2026-06-22T14:03:40+00:00"):
            result = get_job_status_payload(job.job_id)

        assert result["execution_state"] == "attention"
        assert result["needs_attention"] is True
        assert result["running_duration_seconds"] == 220
        assert result["seconds_since_update"] == 30
        assert "download_images" in result["diagnostic_summary"]
        assert result["recommended_poll_after_seconds"] == 5

    def test_marks_stale_running_job_as_stalled(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=100, payload={"tid": 100})
        db.execute(
            """
            UPDATE jobs
            SET status = 'running',
                stage = 'download_images',
                progress_current = 4,
                progress_total = 6,
                created_at = '2026-06-22T14:00:00+00:00',
                updated_at = '2026-06-22T14:01:00+00:00'
            WHERE job_id = ?
            """,
            (job.job_id,),
        )
        db.commit()
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db), \
             patch("yamibo_mcp.server.schemas.utc_now_iso", return_value="2026-06-22T14:03:40+00:00"):
            result = get_job_status_payload(job.job_id)

        assert result["execution_state"] == "stalled"
        assert result["needs_attention"] is True
        assert "no progress update" in result["diagnostic_summary"]
        assert result["recommended_poll_after_seconds"] == 10

    def test_marks_partial_timeout_job_as_attention_with_timeout_summary(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("sync_thread", tid=100, payload={"tid": 100})
        repo.partial(
            job.job_id,
            artifacts={
                "tid": 100,
                "archive_status": "partial",
                "download_stopped_reason": "stage_timeout",
            },
        )
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db):
            result = get_job_status_payload(job.job_id)

        assert result["is_terminal"] is True
        assert result["result_ready"] is True
        assert result["execution_state"] == "terminal"
        assert result["needs_attention"] is True
        assert "download_images timed out" in result["diagnostic_summary"]

    def test_direct_schema_payload_accepts_datetime_fields(self):
        job = MagicMock()
        now = datetime(2026, 7, 6, 0, 50, 0, tzinfo=timezone.utc)
        job.job_id = "job-1"
        job.job_type = "update_thread"
        job.status = "running"
        job.stage = "write_context"
        job.progress_current = 1
        job.progress_total = 2
        job.worker_id = "worker-1"
        job.error_code = None
        job.error_message = None
        job.artifacts = {}
        job.created_at = now
        job.updated_at = now
        job.finished_at = None

        with patch("yamibo_mcp.server.schemas.utc_now_iso", return_value="2026-07-06T00:50:10+00:00"):
            payload = direct_job_status_payload(job)

        assert payload["job_id"] == "job-1"
        assert payload["execution_state"] == "normal"
        assert payload["running_duration_seconds"] == 10
