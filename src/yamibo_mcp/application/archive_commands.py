from __future__ import annotations

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.application.thread_update_use_cases import create_update_thread_job
from yamibo_mcp.application.thread_use_cases import archive_thread_job
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.resources import job_events_uri, thread_summary_uri


def create_thread_archive_job(
    *,
    tid: int | None = None,
    url: str | None = None,
    html_path: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    payload = archive_thread_job(
        tid=tid,
        url=url,
        html_path=html_path,
        base_url=base_url,
        forum_id=forum_id,
    )
    job_id = str(payload["job_id"])
    return AgentResult(
        ok=True,
        data={"job_id": job_id, "status": "queued"},
        resources={"job_events": job_events_uri(job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job_id}, reason="Poll the queued archive job."),
        ],
        side_effects=["sqlite_job_created", "daemon_required"],
    )


def ensure_thread_archived(
    *,
    tid: int,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        thread = ThreadsRepository(conn).get_thread(tid)
    finally:
        conn.close()

    if thread is not None:
        return AgentResult(
            ok=True,
            data={
                "tid": tid,
                "archived": True,
                "archive_status": thread["archive_status"],
                "source": "local_archive",
            },
            resources={"summary": thread_summary_uri(tid)},
        )

    result = create_thread_archive_job(tid=tid, base_url=base_url, forum_id=forum_id)
    if result.data is not None:
        result.data["tid"] = tid
        result.data["archived"] = False
        result.data["source"] = "job_created"
    return result


def create_thread_export_job(*, tid: int, strategy: str | None = None) -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        job = JobsRepository(conn).create(
            JobType.EXPORT_THREAD.value,
            tid=tid,
            payload={key: value for key, value in {"tid": tid, "strategy": strategy}.items() if value is not None},
        )
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={"job_id": job.job_id, "status": "queued", "tid": tid},
        resources={"job_events": job_events_uri(job.job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job.job_id}, reason="Poll the export job until it finishes."),
        ],
        side_effects=["sqlite_job_created", "daemon_required"],
    )


def create_thread_update_job(*, tid: int, base_url: str | None = None) -> AgentResult:
    job_id = str(create_update_thread_job(tid=tid, base_url=base_url)["job_id"])
    return AgentResult(
        ok=True,
        data={"job_id": job_id, "status": "queued", "tid": tid},
        resources={"job_events": job_events_uri(job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job_id}, reason="Poll the update job until it finishes."),
        ],
        side_effects=["sqlite_job_created", "daemon_required"],
    )
