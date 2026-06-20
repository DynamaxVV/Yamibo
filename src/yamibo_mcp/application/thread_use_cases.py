from __future__ import annotations

from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.schemas import build_series_summary, thread_detail_payload
from yamibo_mcp.yamibo.urls import extract_tid_from_input


def ensure_thread(
    *,
    tid: int,
    url: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        thread = repo.get_thread(tid)
        if thread is None:
            sync_result = _run_inline_sync(
                tid=tid,
                url=url,
                base_url=base_url,
                settings=settings,
            )
            if sync_result.get("status") == "failed" or sync_result.get("found") is False:
                return sync_result
            thread = repo.get_thread(tid)
            if thread is None:
                return {
                    "tid": tid,
                    "found": False,
                    "archived": False,
                    "message": f"thread {tid} could not be loaded after remote sync",
                    "sync_job": sync_result,
                }
        title = repo.get_title_parse(tid)
        floors = repo.list_floors(tid)
        payload = thread_detail_payload(thread, title, floors, data_dir=settings.data_dir)
        if thread["series_id"] is not None:
            payload["series"] = build_series_summary(
                int(thread["series_id"]),
                canonical_title=title["core_title_guess"] if title is not None else payload.get("core_title"),
            )
        return payload
    finally:
        conn.close()


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
        job = repo.create(JobType.SYNC_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id}
    finally:
        conn.close()


def _run_inline_sync(
    *,
    tid: int,
    url: str | None = None,
    base_url: str | None = None,
    settings=None,
) -> dict[str, Any]:
    from yamibo_mcp.daemon.handlers.sync_thread import handle_sync_thread

    conn = connect(settings.db_path)
    worker_id = "server_inline_sync"
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        job = repo.create(
            JobType.SYNC_THREAD.value,
            tid=tid,
            payload={
                key: value
                for key, value in {"tid": tid, "url": url, "base_url": base_url}.items()
                if value is not None
            },
        )
        job = repo.acquire(job.job_id, worker_id, settings.worker_lease_seconds)
        try:
            handle_sync_thread(repo, job, worker_id, settings.worker_lease_seconds, settings)
        except Exception as exc:
            repo.fail(job.job_id, exc.__class__.__name__, str(exc))
            return {
                "tid": tid,
                "found": False,
                "archived": False,
                "message": f"failed to sync thread {tid} from remote",
                "sync_job": {"job_id": job.job_id, "status": "failed"},
                "error": {"code": exc.__class__.__name__, "message": str(exc)},
            }
        final_job = repo.get(job.job_id)
        return {
            "job_id": final_job.job_id,
            "status": final_job.status,
            "artifacts": final_job.artifacts,
        }
    finally:
        conn.close()
