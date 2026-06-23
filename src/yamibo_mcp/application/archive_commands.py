from __future__ import annotations

from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.application.update_commands import create_update_thread_job
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.resource_uris import job_events_uri, thread_summary_uri


def _can_reuse_existing_job(existing_payload: dict[str, Any], requested_payload: dict[str, Any]) -> bool:
    return existing_payload == requested_payload


def archive_thread_job(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, Any]:
    if not html_path and not tid and not url:
        raise ValueError("archive_thread requires html_path or tid or url")
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        payload = {
            key: value
            for key, value in {
                "html_path": html_path,
                "tid": tid,
                "url": url,
                "base_url": base_url,
                "forum_id": forum_id,
            }.items()
            if value is not None
        }
        if tid is not None:
            existing = repo.find_live_job_for_thread(job_type=JobType.SYNC_THREAD.value, tid=tid)
            if existing is not None and _can_reuse_existing_job(existing.payload, payload):
                return {"job_id": existing.job_id, "created": False}
        job = repo.create(JobType.SYNC_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id, "created": True}
    finally:
        conn.close()


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
    created = bool(payload.get("created", True))
    return AgentResult(
        ok=True,
        data={"job_id": job_id, "status": "queued", "created": created},
        resources={"job_events": job_events_uri(job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job_id}, reason="Poll the queued archive job."),
        ],
        side_effects=[
            "sqlite_job_created" if created else "sqlite_job_reused",
            "daemon_required",
        ],
    )


def create_thread_archive_batch_jobs(
    *,
    tids: list[int],
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    normalized_tids = list(dict.fromkeys(int(tid) for tid in tids if tid))
    if not normalized_tids:
        raise ValueError("tids required")
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        created_job_ids: list[str] = []
        reused_job_ids: list[str] = []
        for tid in normalized_tids:
            payload = {
                key: value
                for key, value in {
                    "tid": tid,
                    "base_url": base_url,
                    "forum_id": forum_id,
                }.items()
                if value is not None
            }
            existing = repo.find_live_job_for_thread(job_type=JobType.SYNC_THREAD.value, tid=tid)
            if existing is not None and _can_reuse_existing_job(existing.payload, payload):
                reused_job_ids.append(existing.job_id)
                continue
            job = repo.create(JobType.SYNC_THREAD.value, tid=tid, payload=payload)
            created_job_ids.append(job.job_id)
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "job_type": JobType.SYNC_THREAD.value,
            "target_count": len(normalized_tids),
            "created_count": len(created_job_ids),
            "reused_count": len(reused_job_ids),
            "created_job_ids": created_job_ids,
            "reused_job_ids": reused_job_ids,
            "tids": normalized_tids,
        },
        side_effects=["sqlite_job_created" if created_job_ids else "sqlite_job_reused", "daemon_required"],
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
        repo = JobsRepository(conn)
        payload = {key: value for key, value in {"tid": tid, "strategy": strategy}.items() if value is not None}
        job = repo.find_live_job_for_thread(job_type=JobType.EXPORT_THREAD.value, tid=tid)
        created = False
        if job is None or not _can_reuse_existing_job(job.payload, payload):
            job = repo.create(
                JobType.EXPORT_THREAD.value,
                tid=tid,
                payload=payload,
            )
            created = True
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={"job_id": job.job_id, "status": "queued", "tid": tid, "created": created},
        resources={"job_events": job_events_uri(job.job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job.job_id}, reason="Poll the export job until it finishes."),
        ],
        side_effects=[
            "sqlite_job_created" if created else "sqlite_job_reused",
            "daemon_required",
        ],
    )


def create_thread_update_job(*, tid: int, base_url: str | None = None) -> AgentResult:
    payload = create_update_thread_job(tid=tid, base_url=base_url)
    job_id = str(payload["job_id"])
    created = bool(payload.get("created", True))
    return AgentResult(
        ok=True,
        data={"job_id": job_id, "status": "queued", "tid": tid, "created": created},
        resources={"job_events": job_events_uri(job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job_id}, reason="Poll the update job until it finishes."),
        ],
        side_effects=[
            "sqlite_job_created" if created else "sqlite_job_reused",
            "daemon_required",
        ],
    )
