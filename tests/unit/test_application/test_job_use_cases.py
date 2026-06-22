from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from yamibo_mcp.application.job_queries import get_job_status_payload
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.schemas import job_status_payload as direct_job_status_payload


_JOB_STATUS_KEYS = {
    "job_id", "job_type", "status", "stage", "progress_current", "progress_total",
    "worker_id", "error_code", "error_message", "artifacts", "created_at",
    "updated_at", "finished_at", "is_terminal", "result_ready",
    "recommended_poll_after_seconds",
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
