from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.web_fastapi.converters import job_rows_to_dicts, job_to_dict, event_to_dict, job_failure_kind
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.web_fastapi.helpers import TTLCache
from yamibo_mcp.web_fastapi.models.requests import JobActionRequest, JobControlRequest
from yamibo_mcp.web_fastapi.routers.settings import persist_jobs_enabled
from yamibo_mcp.yamibo.anti_bot import clear_remote_access_pause

router = APIRouter(prefix="/api", tags=["jobs"])

_jobs_cache = TTLCache(ttl_seconds=3.0)
_FAILURE_SCAN_BATCH_SIZE = 500


def _clear_jobs_cache() -> None:
    _jobs_cache.clear()


def _iter_jobs(repo: JobsRepository, *, status: str | None):
    offset = 0
    while True:
        batch = repo.list(limit=_FAILURE_SCAN_BATCH_SIZE, offset=offset, status=status)
        if not batch:
            return
        yield from batch
        if len(batch) < _FAILURE_SCAN_BATCH_SIZE:
            return
        offset += len(batch)


def _failure_kind_page(
    repo: JobsRepository,
    *,
    status: str | None,
    failure_kind: str,
    page: int,
    page_size: int,
) -> tuple[int, list]:
    start = (page - 1) * page_size
    end = start + page_size
    total_count = 0
    selected = []
    for job in _iter_jobs(repo, status=status):
        kind = job_failure_kind(job, artifacts=job.artifacts if isinstance(job.artifacts, dict) else {}) or "other"
        if kind != failure_kind:
            continue
        if start <= total_count < end:
            selected.append(job)
        total_count += 1
    return total_count, selected


@router.get("/jobs")
def list_jobs(
    status: str | None = Query(default=None),
    failure_kind: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    conn: DatabaseConnection = Depends(get_conn),
):
    cache_key = f"list:{status}:{failure_kind}:{page}:{page_size}"
    cached = _jobs_cache.get(cache_key)
    if cached is not None:
        return cached

    repo = JobsRepository(conn)
    if failure_kind:
        total_count, jobs = _failure_kind_page(
            repo,
            status=status,
            failure_kind=failure_kind,
            page=page,
            page_size=page_size,
        )
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
            _, jobs = _failure_kind_page(
                repo,
                status=status,
                failure_kind=failure_kind,
                page=page,
                page_size=page_size,
            )
    else:
        total_count = repo.count_filtered(status=status)
        offset = (page - 1) * page_size
        jobs = repo.list(limit=page_size, offset=offset, status=status)

    total_pages = max(1, (total_count + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size
        if not failure_kind:
            jobs = repo.list(limit=page_size, offset=offset, status=status)

    result = {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "status": status,
        "failure_kind": failure_kind,
        "items": job_rows_to_dicts(jobs, conn, include_details=False),
    }
    _jobs_cache.set(cache_key, result)
    return result


@router.get("/jobs/counts")
def job_counts(conn: DatabaseConnection = Depends(get_conn)):
    cached = _jobs_cache.get("counts")
    if cached is not None:
        return cached
    result = JobsRepository(conn).count_by_status()
    _jobs_cache.set("counts", result)
    return result


@router.get("/jobs/failure-counts")
def job_failure_counts(
    status: str | None = Query(default=None),
    conn: DatabaseConnection = Depends(get_conn),
):
    cache_key = f"failure_counts:{status}"
    cached = _jobs_cache.get(cache_key)
    if cached is not None:
        return cached
    counts: dict[str, int] = {}
    for job in _iter_jobs(JobsRepository(conn), status=status):
        kind = job_failure_kind(job, artifacts=job.artifacts if isinstance(job.artifacts, dict) else {}) or "other"
        counts[kind] = counts.get(kind, 0) + 1
    _jobs_cache.set(cache_key, counts)
    return counts


_BACKFILL_STATE_KEY = "image_backfill_auto_scheduler"


@router.get("/jobs/backfill-status")
def backfill_status(conn: DatabaseConnection = Depends(get_conn)):
    cached = _jobs_cache.get("backfill-status")
    if cached is not None:
        return cached
    settings = load_settings()
    state = SystemStateRepository(conn).get_json(_BACKFILL_STATE_KEY) or {}
    today = datetime.now(timezone.utc).date().isoformat()
    state_day = str(state.get("day") or "")
    count = int(state.get("count") or 0) if state_day == today else 0
    pending_count = JobsRepository(conn).count_by_job_type_status(
        job_type=JobType.IMAGE_BACKFILL.value,
        statuses=(
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
            JobStatus.RETRYING.value,
            JobStatus.PAUSED.value,
            JobStatus.CANCEL_REQUESTED.value,
        ),
    )
    result = {
        "enabled": settings.image_backfill_enabled,
        "dry_run": settings.image_backfill_dry_run,
        "forum_id": settings.image_backfill_forum_id,
        "daily_limit": settings.image_backfill_daily_limit,
        "interval_seconds": settings.image_backfill_auto_interval_seconds,
        "max_pages": settings.image_backfill_max_pages,
        "today_count": count,
        "pending_count": pending_count,
        "last_enqueued_at": state.get("last_enqueued_at"),
        "last_reason": state.get("last_reason"),
    }
    _jobs_cache.set("backfill-status", result)
    return result


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, conn: DatabaseConnection = Depends(get_conn)):
    job = JobsRepository(conn).get(job_id)
    return job_to_dict(job, conn)


@router.get("/jobs/{job_id}/events")
def job_events(
    job_id: str,
    since_event_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    conn: DatabaseConnection = Depends(get_conn),
):
    events = JobEventsRepository(conn).list(
        job_id=job_id,
        since_event_id=since_event_id,
        limit=limit + 1,
    )
    has_more = len(events) > limit
    events = events[:limit]
    next_since_event_id = events[-1].event_id if events else since_event_id
    return JSONResponse(
        content=jsonable_encoder([event_to_dict(event) for event in events]),
        headers={
            "X-Has-More": "true" if has_more else "false",
            "X-Next-Event-ID": "" if next_since_event_id is None else str(next_since_event_id),
        },
    )


@router.post("/jobs/delete")
def delete_job(body: JobActionRequest, conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    repo = JobsRepository(conn)
    job = repo.get(body.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("running",):
        raise HTTPException(status_code=400, detail="Cannot delete a running job")
    repo.delete_job(body.job_id)
    return {"ok": True, "job_id": body.job_id}


@router.post("/jobs/retry")
def retry_job(body: JobActionRequest, conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    repo = JobsRepository(conn)
    job = repo.get(body.job_id)
    if job.status not in (JobStatus.PARTIAL.value, JobStatus.FAILED.value, JobStatus.INTERRUPTED.value):
        raise HTTPException(status_code=400, detail="Only partial, failed, or interrupted jobs can be retried")
    try:
        next_job = repo.rerun(job.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "job_id": next_job.job_id,
        "source_job_id": job.job_id,
        "status": next_job.status,
    }


@router.post("/jobs/resync-thread")
def resync_failed_job(body: JobActionRequest, conn: DatabaseConnection = Depends(get_conn)):
    try:
        job, deleted = JobsRepository(conn).resync_failed_floor_job(body.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _clear_jobs_cache()
    return {"ok": True, "job_id": job.job_id, "deleted_failed_count": deleted}


@router.post("/jobs/pause")
def pause_job(body: JobActionRequest, conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    repo = JobsRepository(conn)
    job = repo.get(body.job_id)
    if job.status == JobStatus.PAUSED.value:
        return {"ok": True, "job_id": body.job_id, "status": JobStatus.PAUSED.value}
    if not repo.pause(body.job_id):
        raise HTTPException(status_code=400, detail="Cannot pause job")
    return {"ok": True, "job_id": body.job_id, "status": JobStatus.PAUSED.value}


@router.post("/jobs/resume")
def resume_job(body: JobActionRequest, conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    repo = JobsRepository(conn)
    job = repo.get(body.job_id)
    if job.status != JobStatus.PAUSED.value:
        raise HTTPException(status_code=400, detail="Only paused jobs can be resumed")
    if not repo.resume(body.job_id):
        raise HTTPException(status_code=400, detail="Cannot resume job while it is still owned by a worker")
    return {"ok": True, "job_id": body.job_id, "status": JobStatus.QUEUED.value}


@router.post("/jobs/control")
def job_control(
    body: JobControlRequest,
    conn: DatabaseConnection = Depends(get_conn),
    settings=Depends(get_settings),
):
    _clear_jobs_cache()
    repo = JobsRepository(conn)

    if body.action == "pause":
        persist_jobs_enabled(settings, False)
        changed_job_ids = repo.pause_active_jobs()
        return {
            "ok": True,
            "action": "pause",
            "changed_job_ids": changed_job_ids,
            "changed_count": len(changed_job_ids),
            "job_control": {**repo.job_control_summary(), "jobs_enabled": False},
        }
    if body.action == "resume":
        persist_jobs_enabled(settings, True)
        changed_job_ids = repo.resume_paused_jobs()
        return {
            "ok": True,
            "action": "resume",
            "changed_job_ids": changed_job_ids,
            "changed_count": len(changed_job_ids),
            "job_control": {**repo.job_control_summary(), "jobs_enabled": True},
        }
    raise HTTPException(status_code=400, detail="action must be pause or resume")


@router.post("/remote-access/resume")
def resume_remote_access(conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    return clear_remote_access_pause(conn)


@router.post("/jobs/batch-delete")
def batch_delete_jobs(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    status_filter = body.get("status")
    failure_kind_filter = body.get("failure_kind")
    if not status_filter:
        raise HTTPException(status_code=400, detail="status required")
    if status_filter == "running":
        raise HTTPException(status_code=400, detail="Cannot batch delete running jobs")
    repo = JobsRepository(conn)
    if failure_kind_filter:
        if status_filter != JobStatus.FAILED.value:
            raise HTTPException(status_code=400, detail="failure_kind requires failed status")
        all_failed_jobs = _iter_jobs(repo, status=JobStatus.FAILED.value)
        job_ids = [
            job.job_id
            for job in all_failed_jobs
            if (job_failure_kind(job, artifacts=job.artifacts if isinstance(job.artifacts, dict) else {}) or "other") == failure_kind_filter
        ]
    else:
        job_ids = repo.list_ids_by_status(status_filter)
    if not job_ids:
        return {"ok": True, "deleted": 0}
    repo.delete_jobs(job_ids)
    return {"ok": True, "deleted": len(job_ids)}


@router.post("/jobs/batch-delete-ids")
def batch_delete_jobs_by_ids(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    job_ids = body.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids required")
    running = JobsRepository(conn).list_running_job_ids([str(jid) for jid in job_ids])
    if running:
        raise HTTPException(status_code=400, detail="Cannot delete running jobs")
    JobsRepository(conn).delete_jobs(job_ids)
    return {"ok": True, "deleted": len(job_ids)}


@router.post("/jobs/merge-queued")
def merge_queued_jobs(conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    result = JobsRepository(conn).merge_queued_duplicates()
    return {"ok": True, **result}


@router.post("/jobs/safe-delete")
def safe_delete_job(body: JobActionRequest, conn: DatabaseConnection = Depends(get_conn)):
    _clear_jobs_cache()
    repo = JobsRepository(conn)
    job = repo.get(body.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        ok = repo.request_cancel(body.job_id)
        if not ok:
            raise HTTPException(status_code=400, detail="Failed to request cancellation")
        return {"ok": True, "action": "cancel_requested", "job_id": body.job_id}
    else:
        JobsRepository(conn).delete_job(body.job_id)
        return {"ok": True, "action": "deleted", "job_id": body.job_id}
