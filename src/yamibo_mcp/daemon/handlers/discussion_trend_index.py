"""Discussion trend V1 index job handler.

V1 scope (per Step 03 spec):
- orchestrate trend mart build + atomic current-pointer switch + retention cleanup
- enforce input validation, postgres-only backend, fail-fast on bad payload
- mark previous current run as superseded before retention
- never touch current run, daily mart tables, run metadata, or report artifacts
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from yamibo_mcp.application.discussion_trend_builder import (
    BuildInput,
    build_trend_mart,
)
from yamibo_mcp.db.repositories.discussion_trends import (
    DiscussionTrendRepository,
    ensure_postgres,
)
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.domain.models import Job

LOG = logging.getLogger(__name__)

_TOTAL_STAGES = 6
_DEFAULT_RETENTION_SUCCESS_RUNS = 3
_DEFAULT_VERSION = "trend-v1"

# Stable error codes (Step 03 contract).
_ERR_INVALID_PAYLOAD = "DISCUSSION_TREND_INVALID_PAYLOAD"
_ERR_POSTGRES_REQUIRED = "DISCUSSION_TREND_POSTGRES_REQUIRED"
_ERR_BUILD_FAILED = "DISCUSSION_TREND_BUILD_FAILED"


def handle_discussion_trend_index(repo, job: Job, worker_id: str, lease_seconds: int, settings) -> None:
    """Daemon handler entrypoint for JobType.DISCUSSION_TREND_INDEX.

    Stage map: validate -> lock -> build -> verify -> publish -> cleanup.
    The builder internally acquires the window lock, writes the mart, marks the
    run succeeded, and upserts the current pointer atomically (w.r.t. the
    underlying connection). This handler adds input validation, the
    publish-supersede step, and retention cleanup on top.
    """
    payload, error = _parse_payload(job)
    if error is not None:
        LOG.warning("[discussion-trend] %s job_id=%s payload_error=%s", _ERR_INVALID_PAYLOAD, job.job_id, error)
        repo.fail(job.job_id, _ERR_INVALID_PAYLOAD, error, artifacts={"stage": "validate"})
        return

    try:
        ensure_postgres(repo.conn)
    except ValueError as exc:
        msg = str(exc)
        LOG.warning("[discussion-trend] %s job_id=%s %s", _ERR_POSTGRES_REQUIRED, job.job_id, msg)
        repo.fail(job.job_id, _ERR_POSTGRES_REQUIRED, msg, artifacts={"stage": "validate"})
        return

    run_id = job.job_id  # bind run_id to job_id for traceability
    forum_id = int(payload["forum_id"])
    start_iso = str(payload["start_date"])
    end_iso = str(payload["end_date"])
    version = str(payload["version"])
    retention = int(payload["retention_success_runs"])
    thresholds = payload.get("thresholds") or {}

    repo.update_stage(job.job_id, "validate", progress_current=1, progress_total=_TOTAL_STAGES)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)

    # Snapshot the previous current run (if any) BEFORE the builder mutates the
    # pointer, so we can mark it superseded afterwards.
    trend_repo = DiscussionTrendRepository(repo.conn)
    previous_current = trend_repo.get_current_run(
        forum_id=forum_id,
        start_date=start_iso,
        end_date=end_iso,
        version=version,
    )

    repo.update_stage(job.job_id, "lock", progress_current=2, progress_total=_TOTAL_STAGES)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)

    repo.update_stage(job.job_id, "build", progress_current=3, progress_total=_TOTAL_STAGES)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    try:
        build_result = build_trend_mart(
            repo.conn,
            run_id=run_id,
            payload=BuildInput(
                forum_id=forum_id,
                start_date=_parse_date(start_iso),
                end_date=_parse_date(end_iso),
                version=version,
                thresholds=thresholds,
            ),
        )
    except ValueError as exc:
        # Builder input validation failed after handler validation slipped through.
        msg = str(exc)
        LOG.warning("[discussion-trend] %s job_id=%s %s", _ERR_INVALID_PAYLOAD, job.job_id, msg)
        try:
            trend_repo.mark_run_failed(
                run_id,
                error_json={"code": _ERR_INVALID_PAYLOAD, "message": msg},
            )
        except Exception:  # noqa: BLE001 - best effort
            LOG.exception("[discussion-trend] failed to mark run failed run_id=%s", run_id)
        repo.fail(
            job.job_id,
            _ERR_INVALID_PAYLOAD,
            msg,
            artifacts={
                "stage": "build",
                "run_id": run_id,
                "forum_id": forum_id,
                "start_date": start_iso,
                "end_date": end_iso,
                "version": version,
            },
        )
        return
    except Exception as exc:  # noqa: BLE001 - mapped to stable code per spec
        msg = f"{exc.__class__.__name__}: {exc}"
        LOG.exception("[discussion-trend] %s job_id=%s %s", _ERR_BUILD_FAILED, job.job_id, msg)
        try:
            trend_repo.mark_run_failed(
                run_id,
                error_json={"code": _ERR_BUILD_FAILED, "message": msg},
            )
        except Exception:  # noqa: BLE001 - best effort
            LOG.exception("[discussion-trend] failed to mark run failed run_id=%s", run_id)
        repo.fail(
            job.job_id,
            _ERR_BUILD_FAILED,
            msg,
            artifacts={
                "stage": "build",
                "run_id": run_id,
                "forum_id": forum_id,
                "start_date": start_iso,
                "end_date": end_iso,
                "version": version,
            },
        )
        return

    repo.update_stage(job.job_id, "verify", progress_current=4, progress_total=_TOTAL_STAGES)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    if build_result.status != "succeeded":
        # Builder should only ever return 'succeeded' on the happy path; treat
        # anything else as a build failure so the job fails fast.
        msg = f"builder returned status={build_result.status!r}"
        try:
            trend_repo.mark_run_failed(
                run_id,
                error_json={"code": _ERR_BUILD_FAILED, "message": msg},
            )
        except Exception:  # noqa: BLE001 - best effort
            LOG.exception("[discussion-trend] failed to mark run failed run_id=%s", run_id)
        repo.fail(
            job.job_id,
            _ERR_BUILD_FAILED,
            msg,
            artifacts={
                "stage": "verify",
                "run_id": run_id,
                "build_status": build_result.status,
            },
        )
        return

    repo.update_stage(job.job_id, "publish", progress_current=5, progress_total=_TOTAL_STAGES)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    if previous_current is not None and previous_current.run_id != run_id:
        try:
            trend_repo.mark_run_superseded(previous_current.run_id)
        except Exception:  # noqa: BLE001 - log + continue; current pointer already published
            LOG.exception(
                "[discussion-trend] failed to mark previous current as superseded previous_run_id=%s",
                previous_current.run_id,
            )

    repo.update_stage(job.job_id, "cleanup", progress_current=6, progress_total=_TOTAL_STAGES)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    try:
        retention_summary = trend_repo.apply_retention(
            forum_id=forum_id,
            start_date=start_iso,
            end_date=end_iso,
            version=version,
            current_run_id=run_id,
            keep_succeeded_runs=retention,
        )
    except Exception as exc:  # noqa: BLE001 - retention failure is non-fatal
        LOG.exception("[discussion-trend] retention failed job_id=%s", job.job_id)
        retention_summary = {
            "deleted_assignments": 0,
            "deleted_rag_chunk_topics": 0,
            "runs_pruned": 0,
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    repo.succeed(
        job.job_id,
        artifacts={
            "run_id": run_id,
            "forum_id": forum_id,
            "start_date": start_iso,
            "end_date": end_iso,
            "version": version,
            "warning_summary": list(build_result.warnings),
            "metrics": dict(build_result.metrics),
            "previous_current_run_id": (
                None if previous_current is None else previous_current.run_id
            ),
            "retention": retention_summary,
        },
    )


def _parse_payload(job: Job) -> tuple[dict[str, Any], str | None]:
    payload = job.payload if isinstance(job.payload, dict) else {}
    forum_id = payload.get("forum_id")
    if not isinstance(forum_id, int) or isinstance(forum_id, bool) or forum_id <= 0:
        return {}, "forum_id must be a positive integer"
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if not isinstance(start_date, str) or not isinstance(end_date, str):
        return {}, "start_date and end_date must be ISO date strings (YYYY-MM-DD)"
    try:
        _parse_date(start_date)
        _parse_date(end_date)
    except ValueError:
        return {}, "start_date and end_date must be ISO date strings (YYYY-MM-DD)"
    if start_date > end_date:
        return {}, "start_date must be <= end_date"
    version = payload.get("version", _DEFAULT_VERSION)
    if not isinstance(version, str) or not version:
        return {}, "version must be a non-empty string"
    retention = payload.get("retention_success_runs", _DEFAULT_RETENTION_SUCCESS_RUNS)
    if not isinstance(retention, int) or isinstance(retention, bool) or retention < 0:
        return {}, "retention_success_runs must be a non-negative integer"
    thresholds = payload.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        return {}, "thresholds must be an object"
    return (
        {
            "forum_id": forum_id,
            "start_date": start_date,
            "end_date": end_date,
            "version": version,
            "thresholds": thresholds,
            "retention_success_runs": retention,
        },
        None,
    )


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
