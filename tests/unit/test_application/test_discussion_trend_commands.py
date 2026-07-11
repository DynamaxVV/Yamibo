"""Unit tests for create_discussion_trend_index_job (Step 03)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yamibo_mcp.application.discussion_trend_commands import (
    create_discussion_trend_index_job,
)
from yamibo_mcp.domain.models import Job


def _fake_job(job_id: str = "trend_idx_abc", **payload) -> Job:
    return Job(
        job_id=job_id,
        job_type="discussion_trend_index",
        status="queued",
        stage="validate",
        tid=None,
        payload=payload,
        progress_current=0,
        progress_total=None,
        worker_id=None,
        heartbeat_at=None,
        lease_until=None,
        retry_count=0,
        max_retries=0,
        resumable=True,
        error_code=None,
        error_message=None,
        artifacts={},
        paused_at=None,
        created_at="t0",
        updated_at="t0",
        finished_at=None,
    )


def test_create_job_happy_path_persists_job_and_returns_agent_result():
    fake_jobs_repo = MagicMock()
    fake_jobs_repo.create.return_value = _fake_job(job_id="trend_idx_xyz")
    fake_conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_commands.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_commands.connect", return_value=fake_conn) as connect, \
         patch("yamibo_mcp.application.discussion_trend_commands.JobsRepository", return_value=fake_jobs_repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = create_discussion_trend_index_job(
            forum_id=5,
            start_date="2014-11-01",
            end_date="2014-11-30",
        )

    assert result.ok is True
    assert result.data["job_id"] == "trend_idx_xyz"
    assert result.data["created"] is True
    assert result.data["job_type"] == "discussion_trend_index"
    assert result.data["forum_id"] == 5
    assert result.data["start_date"] == "2014-11-01"
    assert result.data["end_date"] == "2014-11-30"
    assert result.data["version"] == "trend-v1"
    assert result.data["retention_success_runs"] == 3
    assert result.side_effects == ["job_created"]

    fake_jobs_repo.create.assert_called_once()
    args, kwargs = fake_jobs_repo.create.call_args
    assert args[0] == "discussion_trend_index"
    payload = kwargs["payload"]
    assert payload["forum_id"] == 5
    assert payload["start_date"] == "2014-11-01"
    assert payload["end_date"] == "2014-11-30"
    assert payload["version"] == "trend-v1"
    assert payload["retention_success_runs"] == 3
    # Thresholds is omitted from the payload when caller passed nothing.
    assert "thresholds" not in payload
    fake_conn.close.assert_called_once()


def test_create_job_propagates_thresholds_and_retention():
    fake_jobs_repo = MagicMock()
    fake_jobs_repo.create.return_value = _fake_job()
    fake_conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_commands.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_commands.connect", return_value=fake_conn), \
         patch("yamibo_mcp.application.discussion_trend_commands.JobsRepository", return_value=fake_jobs_repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = create_discussion_trend_index_job(
            forum_id=33,
            start_date="2014-11-15",
            end_date="2014-11-15",
            version="custom-v1",
            thresholds={"min_floor_count": 50, "min_topics": 5},
            retention_success_runs=7,
        )

    assert result.ok is True
    payload = fake_jobs_repo.create.call_args.kwargs["payload"]
    assert payload["thresholds"] == {"min_floor_count": 50, "min_topics": 5}
    assert payload["retention_success_runs"] == 7
    assert payload["version"] == "custom-v1"


@pytest.mark.parametrize(
    "kwargs, expected_field",
    [
        ({"forum_id": 0, "start_date": "2024-01-01", "end_date": "2024-01-31"}, "forum_id"),
        ({"forum_id": -1, "start_date": "2024-01-01", "end_date": "2024-01-31"}, "forum_id"),
        ({"forum_id": 5, "start_date": "2024-02-01", "end_date": "2024-01-01"}, "start_date"),
        ({"forum_id": 5, "start_date": "bad", "end_date": "2024-01-31"}, "start_date"),
        ({"forum_id": 5, "start_date": "2024-01-01", "end_date": "2024-01-01", "retention_success_runs": -2}, "retention_success_runs"),
        ({"forum_id": 5, "start_date": "2024-01-01", "end_date": "2024-01-31", "version": ""}, "version"),
    ],
)
def test_create_job_rejects_invalid_payload(kwargs, expected_field):
    result = create_discussion_trend_index_job(**kwargs)
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "DISCUSSION_TREND_INVALID_PAYLOAD"
    assert result.error.retryable is False
    fields = {fe["field"] for fe in result.error.field_errors}
    assert expected_field in fields
