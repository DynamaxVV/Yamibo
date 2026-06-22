from __future__ import annotations

import json
import time
from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.errors import JobNotFound
from yamibo_mcp.server.resource_uris import job_events_uri, job_status_uri
from yamibo_mcp.server.schemas import job_status_payload


def get_job_status_payload(job_id: str) -> dict[str, Any]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        job = JobsRepository(conn).get(job_id)
        return job_status_payload(job)
    finally:
        conn.close()


def read_job(*, job_id: str) -> AgentResult:
    try:
        payload = get_job_status_payload(job_id)
    except JobNotFound:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="JOB_NOT_FOUND",
                message=f"Job {job_id} was not found.",
                agent_hint="Check the job id or create a new job and poll that id instead.",
            ),
        )
    return AgentResult(
        ok=True,
        data=payload,
        resources={"status": job_status_uri(job_id), "job_events": job_events_uri(job_id)},
    )


def read_job_events(*, job_id: str) -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        exists = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if exists is None:
            return AgentResult(
                ok=False,
                error=AgentError(
                    code="JOB_NOT_FOUND",
                    message=f"Job {job_id} was not found.",
                    agent_hint="Check the job id or create a new job first.",
                ),
            )
        rows = conn.execute(
            """
            SELECT event_id, job_id, event_type, status, stage, payload_json, created_at
            FROM job_events
            WHERE job_id = ?
            ORDER BY event_id ASC
            """,
            (job_id,),
        ).fetchall()
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "job_id": job_id,
            "events": [
                {
                    "event_id": row["event_id"],
                    "event_type": row["event_type"],
                    "status": row["status"],
                    "stage": row["stage"],
                    "payload": json.loads(row["payload_json"] or "{}"),
                    "payload_json": row["payload_json"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
        },
        resources={"status": job_status_uri(job_id), "job_events": job_events_uri(job_id)},
    )


def wait_for_job(
    *,
    job_id: str,
    timeout_seconds: float = 120,
    poll_interval_seconds: float = 2,
    include_events: bool = False,
) -> AgentResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] | None = None

    while True:
        payload = get_job_status_payload(job_id)
        last_payload = payload
        if payload["is_terminal"]:
            data: dict[str, Any] = {
                **payload,
                "timed_out": False,
                "wait_timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            }
            if include_events:
                events_result = read_job_events(job_id=job_id)
                data["events"] = [] if events_result.data is None else events_result.data["events"]
            return AgentResult(
                ok=True,
                data=data,
                resources={"status": job_status_uri(job_id), "job_events": job_events_uri(job_id)},
            )

        now = time.monotonic()
        if now >= deadline:
            data = {
                **payload,
                "timed_out": True,
                "wait_timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            }
            if include_events:
                events_result = read_job_events(job_id=job_id)
                data["events"] = [] if events_result.data is None else events_result.data["events"]
            return AgentResult(
                ok=True,
                data=data,
                resources={"status": job_status_uri(job_id), "job_events": job_events_uri(job_id)},
                next_actions=[
                    AgentAction(
                        tool="read_job",
                        args={"job_id": job_id},
                        reason="Inspect the latest job status after wait_for_job timed out.",
                    )
                ],
                warnings=[
                    "wait_for_job reached the timeout before the job became terminal."
                ],
            )
        time.sleep(min(poll_interval_seconds, max(deadline - now, 0.0)))
