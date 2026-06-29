from __future__ import annotations

from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.forums import resolve_forum
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.yamibo.anti_bot import ensure_remote_access_allowed


def create_update_thread_job(*, tid: int, base_url: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        ensure_remote_access_allowed(conn)
        repo = JobsRepository(conn)
        payload = {"tid": tid}
        if base_url:
            payload["base_url"] = base_url
        existing = repo.find_live_job_for_thread(job_type=JobType.UPDATE_THREAD.value, tid=tid)
        if existing is not None and existing.payload == payload:
            return {"job_id": existing.job_id, "created": False}
        job = repo.create(JobType.UPDATE_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id, "created": True}
    finally:
        conn.close()


def create_update_thread_batch_jobs(
    *,
    tids: list[int],
    base_url: str | None = None,
) -> AgentResult:
    normalized_tids = list(dict.fromkeys(int(tid) for tid in tids if tid))
    if not normalized_tids:
        raise ValueError("tids required")
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        ensure_remote_access_allowed(conn)
        repo = JobsRepository(conn)
        threads_repo = ThreadsRepository(conn)
        created_job_ids: list[str] = []
        reused_job_ids: list[str] = []
        skipped: list[dict[str, object]] = []
        for tid in normalized_tids:
            thread = threads_repo.get_thread(tid)
            if thread is None:
                skipped.append({"tid": tid, "reason": "thread not found"})
                continue
            forum_id = thread["forum_id"] if "forum_id" in thread.keys() else None
            content_kind = thread["content_kind"] if "content_kind" in thread.keys() else None
            forum = resolve_forum(forum_id) if forum_id is not None else None
            if content_kind != "novel" and (forum is None or forum.content_kind != "novel"):
                skipped.append({"tid": tid, "reason": "not a novel forum thread"})
                continue
            payload = {"tid": tid}
            if base_url:
                payload["base_url"] = base_url
            existing = repo.find_live_job_for_thread(job_type=JobType.UPDATE_THREAD.value, tid=tid)
            if existing is not None and existing.payload == payload:
                reused_job_ids.append(existing.job_id)
                continue
            job = repo.create(JobType.UPDATE_THREAD.value, tid=tid, payload=payload)
            created_job_ids.append(job.job_id)
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "job_type": JobType.UPDATE_THREAD.value,
            "target_count": len(normalized_tids),
            "skipped_count": len(skipped),
            "skipped_tids": [row["tid"] for row in skipped],
            "created_count": len(created_job_ids),
            "reused_count": len(reused_job_ids),
            "created_job_ids": created_job_ids,
            "reused_job_ids": reused_job_ids,
            "tids": normalized_tids,
        },
        warnings=[f"skipped tid {row['tid']}: {row['reason']}" for row in skipped],
        side_effects=["sqlite_job_created" if created_job_ids else "sqlite_job_reused", "daemon_required"],
    )
