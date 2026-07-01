"""Discussion trend V1 report job handler.

Step 05 — consumes DISCUSSION_TREND_REPORT jobs, generates template-driven
JSON + Markdown artifacts, and stores them in discussion_report_runs.
"""

from __future__ import annotations

import logging
from typing import Any

from yamibo_mcp.application.discussion_report import (
    generate_forum_research_report,
    generate_trend_report,
)
from yamibo_mcp.db.repositories.discussion_trends import (
    DiscussionTrendRepository,
    ensure_postgres,
)
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.domain.models import Job

LOG = logging.getLogger(__name__)

_TREND_REPORT_KIND = "trend_report"
_RESEARCH_REPORT_KIND = "forum_research"

_ERR_POSTGRES_REQUIRED = "DISCUSSION_TREND_POSTGRES_REQUIRED"
_ERR_NO_CURRENT_RUN = "DISCUSSION_TREND_CURRENT_NOT_FOUND"
_ERR_REPORT_FAILED = "DISCUSSION_TREND_REPORT_FAILED"
_ERR_INVALID_PAYLOAD = "DISCUSSION_TREND_INVALID_PAYLOAD"


def handle_discussion_trend_report(
    repo, job: Job, worker_id: str, lease_seconds: int, settings
) -> None:
    """Daemon handler entrypoint for JobType.DISCUSSION_TREND_REPORT."""
    payload = job.payload if isinstance(job.payload, dict) else {}
    report_kind = payload.get("report_kind", _TREND_REPORT_KIND)

    try:
        ensure_postgres(repo.conn)
    except ValueError as exc:
        msg = str(exc)
        LOG.warning(
            "[discussion-trend-report] %s job_id=%s %s",
            _ERR_POSTGRES_REQUIRED,
            job.job_id,
            msg,
        )
        repo.fail(job.job_id, _ERR_POSTGRES_REQUIRED, msg, artifacts={"stage": "validate"})
        return

    repo.update_stage(job.job_id, "generate", progress_current=1, progress_total=2)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)

    try:
        if report_kind == _RESEARCH_REPORT_KIND:
            _handle_research_report(repo, job, worker_id, lease_seconds)
        else:
            _handle_trend_report(repo, job, worker_id, lease_seconds)
    except Exception as exc:  # noqa: BLE001
        msg = f"{exc.__class__.__name__}: {exc}"
        LOG.exception(
            "[discussion-trend-report] %s job_id=%s %s",
            _ERR_REPORT_FAILED,
            job.job_id,
            msg,
        )
        repo.fail(
            job.job_id,
            _ERR_REPORT_FAILED,
            msg,
            artifacts={"stage": "generate", "report_kind": report_kind},
        )


def _handle_trend_report(repo, job, worker_id, lease_seconds) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    forum_id = int(payload["forum_id"])
    start_date = str(payload["start_date"])
    end_date = str(payload["end_date"])
    version = str(payload["version"])
    period = str(payload.get("period", "monthly"))

    trend_repo = DiscussionTrendRepository(repo.conn)

    # Validate current run exists
    run = trend_repo.get_current_run(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        version=version,
    )
    if run is None:
        msg = (
            f"No current trend run for forum_id={forum_id} "
            f"window=[{start_date}, {end_date}] version={version}."
        )
        LOG.warning(
            "[discussion-trend-report] %s job_id=%s %s",
            _ERR_NO_CURRENT_RUN,
            job.job_id,
            msg,
        )
        repo.fail(
            job.job_id,
            _ERR_NO_CURRENT_RUN,
            msg,
            artifacts={
                "stage": "generate",
                "report_kind": _TREND_REPORT_KIND,
                "forum_id": forum_id,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        return

    if run.status != "succeeded":
        msg = f"Current run {run.run_id} has status={run.status}, not succeeded."
        LOG.warning(
            "[discussion-trend-report] %s job_id=%s %s",
            _ERR_NO_CURRENT_RUN,
            job.job_id,
            msg,
        )
        repo.fail(
            job.job_id,
            _ERR_NO_CURRENT_RUN,
            msg,
            artifacts={"stage": "generate", "run_id": run.run_id, "run_status": run.status},
        )
        return

    report_json, report_markdown = generate_trend_report(
        repo=trend_repo,
        run=run,
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        version=version,
        period=period,
        conn=trend_repo.conn,
    )

    trend_repo.insert_report_run(
        run_id=run.run_id,
        report_kind=_TREND_REPORT_KIND,
        report_json=report_json,
        report_markdown=report_markdown,
    )

    repo.succeed(
        job.job_id,
        artifacts={
            "run_id": run.run_id,
            "report_kind": _TREND_REPORT_KIND,
            "forum_id": forum_id,
            "start_date": start_date,
            "end_date": end_date,
            "version": version,
            "period": period,
            "warnings": report_json.get("warnings", []),
        },
    )


def _handle_research_report(repo, job, worker_id, lease_seconds) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    forum_id = int(payload["forum_id"])
    start_date = str(payload["start_date"])
    end_date = str(payload["end_date"])
    question = str(payload["question"])
    intent = str(payload.get("intent", "general_research"))
    evidence_query = str(payload.get("query", question))

    trend_repo = DiscussionTrendRepository(repo.conn)

    # Try to find a current run for trend context (optional)
    run = trend_repo.get_current_run(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        version="trend-v1",
    )
    if run is not None and run.status != "succeeded":
        run = None  # Don't use non-succeeded runs for context

    report_json, report_markdown = generate_forum_research_report(
        repo=trend_repo if run else None,
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        question=question,
        intent=intent,
        evidence_query=evidence_query,
        run=run,
        conn=repo.conn,
    )

    # Store with run_id; use the run's ID if available, otherwise use job_id
    store_run_id = run.run_id if run else f"research:{job.job_id}"
    # Ensure a minimal run row exists for FK if no real run
    if run is None:
        try:
            trend_repo.create_run(
                run_id=store_run_id,
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
                version="trend-v1",
                status="research_ephemeral",
            )
        except Exception:  # noqa: BLE001
            LOG.exception(
                "[discussion-trend-report] failed to create ephemeral run for research report job_id=%s",
                job.job_id,
            )
            repo.fail(
                job.job_id,
                _ERR_REPORT_FAILED,
                "Failed to create ephemeral run for research report storage.",
                artifacts={"stage": "generate", "report_kind": _RESEARCH_REPORT_KIND},
            )
            return

    trend_repo.insert_report_run(
        run_id=store_run_id,
        report_kind=_RESEARCH_REPORT_KIND,
        report_json=report_json,
        report_markdown=report_markdown,
    )

    repo.succeed(
        job.job_id,
        artifacts={
            "run_id": store_run_id,
            "report_kind": _RESEARCH_REPORT_KIND,
            "forum_id": forum_id,
            "start_date": start_date,
            "end_date": end_date,
            "question": question,
            "intent": intent,
            "warnings": report_json.get("warnings", []),
        },
    )
