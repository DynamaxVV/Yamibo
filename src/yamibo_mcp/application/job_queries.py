from __future__ import annotations

from yamibo_mcp.application.contracts import AgentError, AgentResult
from yamibo_mcp.application.job_use_cases import get_job_status_payload
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.errors import JobNotFound


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
    return AgentResult(ok=True, data=payload)


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
                    "payload_json": row["payload_json"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
        },
    )
