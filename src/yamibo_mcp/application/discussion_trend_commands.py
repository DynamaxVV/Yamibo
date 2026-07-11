"""Discussion trend V1 application commands.

V1 scope (per Step 03 spec):
- create_discussion_trend_index_job validates payload, persists a queued job,
  and returns a stable AgentResult envelope.
- no live-job reuse: a fresh job_id is created for every call (V1 is
  idempotent at the run-id level via discussion_current_indexes ON CONFLICT).
"""

from __future__ import annotations

from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.resource_uris import job_events_uri

DEFAULT_VERSION = "trend-v1"
DEFAULT_RETENTION_SUCCESS_RUNS = 3


def create_discussion_trend_index_job(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str = DEFAULT_VERSION,
    thresholds: dict[str, float | int] | None = None,
    retention_success_runs: int = DEFAULT_RETENTION_SUCCESS_RUNS,
) -> AgentResult:
    field_errors = _validate(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        version=version,
        thresholds=thresholds,
        retention_success_runs=retention_success_runs,
    )
    if field_errors:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="DISCUSSION_TREND_INVALID_PAYLOAD",
                message="One or more trend index job fields are invalid.",
                agent_hint="Provide forum_id (>0), ISO start_date <= end_date, non-empty version, "
                "non-negative retention_success_runs, and an optional thresholds object.",
                retryable=False,
                field_errors=field_errors,
            ),
        )

    payload: dict[str, Any] = {
        "forum_id": int(forum_id),
        "start_date": start_date,
        "end_date": end_date,
        "version": version,
        "retention_success_runs": int(retention_success_runs),
    }
    if thresholds:
        payload["thresholds"] = dict(thresholds)

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        repo = JobsRepository(conn)
        job = repo.create(JobType.DISCUSSION_TREND_INDEX.value, payload=payload)
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "job_id": job.job_id,
            "job_type": JobType.DISCUSSION_TREND_INDEX.value,
            "created": True,
            "forum_id": int(forum_id),
            "start_date": start_date,
            "end_date": end_date,
            "version": version,
            "retention_success_runs": int(retention_success_runs),
        },
        resources={"job_events": job_events_uri(job.job_id)},
        next_actions=[
            AgentAction(
                tool="read_job",
                args={"job_id": job.job_id},
                reason="Poll the queued discussion trend index job until terminal state.",
            ),
        ],
        side_effects=["job_created"],
    )


def _validate(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str,
    thresholds: dict[str, float | int] | None,
    retention_success_runs: int,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(forum_id, int) or isinstance(forum_id, bool) or forum_id <= 0:
        errors.append({"field": "forum_id", "message": "forum_id must be a positive integer"})
    if not isinstance(start_date, str) or not _is_iso_date(start_date):
        errors.append({"field": "start_date", "message": "start_date must be an ISO date (YYYY-MM-DD)"})
    if not isinstance(end_date, str) or not _is_iso_date(end_date):
        errors.append({"field": "end_date", "message": "end_date must be an ISO date (YYYY-MM-DD)"})
    if (
        isinstance(start_date, str)
        and isinstance(end_date, str)
        and _is_iso_date(start_date)
        and _is_iso_date(end_date)
        and start_date > end_date
    ):
        errors.append({"field": "start_date", "message": "start_date must be <= end_date"})
    if not isinstance(version, str) or not version:
        errors.append({"field": "version", "message": "version must be a non-empty string"})
    if (
        not isinstance(retention_success_runs, int)
        or isinstance(retention_success_runs, bool)
        or retention_success_runs < 0
    ):
        errors.append(
            {
                "field": "retention_success_runs",
                "message": "retention_success_runs must be a non-negative integer",
            }
        )
    if thresholds is not None and not isinstance(thresholds, dict):
        errors.append({"field": "thresholds", "message": "thresholds must be an object"})
    return errors


def _is_iso_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    yyyy, mm, dd = value.split("-")
    return yyyy.isdigit() and mm.isdigit() and dd.isdigit() and 1 <= int(mm) <= 12 and 1 <= int(dd) <= 31
