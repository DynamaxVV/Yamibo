from __future__ import annotations

from http import HTTPStatus

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.yamibo.anti_bot import clear_remote_access_pause
from ._helpers import json_response, error_response, read_json_body, TTLCache
from ._converters import job_rows_to_dicts, job_to_dict, event_to_dict, job_failure_kind
from .settings import persist_jobs_enabled

# 短 TTL 缓存：任务列表查询频繁轮询但数据变化不频繁。
# 写入操作（删除、重试、暂停、恢复、批量操作）清除缓存。
_jobs_cache = TTLCache(ttl_seconds=3.0)


def clear_jobs_cache() -> None:
    _jobs_cache.clear()


def handle_jobs_list(handler, params, conn):
    status = params.get("status", [None])[0]
    failure_kind = params.get("failure_kind", [None])[0]
    try:
        page = max(1, int(params.get("page", ["1"])[0] or 1))
    except ValueError:
        page = 1
    try:
        page_size = int(params.get("page_size", ["25"])[0] or 25)
    except ValueError:
        page_size = 25
    page_size = min(max(page_size, 1), 100)

    cache_key = f"list:{status}:{failure_kind}:{page}:{page_size}"
    cached = _jobs_cache.get(cache_key)
    if cached is not None:
        json_response(handler, cached)
        return

    repo = JobsRepository(conn)
    if failure_kind:
        filtered_jobs = [
            job for job in repo.list(limit=None, status=status)
            if job_failure_kind(job, artifacts=job.artifacts if isinstance(job.artifacts, dict) else {}) == failure_kind
        ]
        total_count = len(filtered_jobs)
        offset = (page - 1) * page_size
        jobs = filtered_jobs[offset:offset + page_size]
    else:
        total_count = repo.count_filtered(status=status)
        offset = (page - 1) * page_size
        jobs = repo.list(limit=page_size, offset=offset, status=status)

    total_pages = max(1, (total_count + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size
        jobs = filtered_jobs[offset:offset + page_size] if failure_kind else repo.list(limit=page_size, offset=offset, status=status)

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
    json_response(handler, result)


def handle_jobs_counts(handler, conn):
    cached = _jobs_cache.get("counts")
    if cached is not None:
        json_response(handler, cached)
        return
    result = JobsRepository(conn).count_by_status()
    _jobs_cache.set("counts", result)
    json_response(handler, result)


def handle_jobs_failure_counts(handler, params, conn):
    status = params.get("status", [None])[0]
    cache_key = f"failure_counts:{status}"
    cached = _jobs_cache.get(cache_key)
    if cached is not None:
        json_response(handler, cached)
        return
    counts: dict[str, int] = {}
    for job in JobsRepository(conn).list(limit=None, status=status):
        kind = job_failure_kind(job, artifacts=job.artifacts if isinstance(job.artifacts, dict) else {}) or "other"
        counts[kind] = counts.get(kind, 0) + 1
    _jobs_cache.set(cache_key, counts)
    json_response(handler, counts)


def handle_job_detail(handler, job_id, conn):
    job = JobsRepository(conn).get(job_id)
    json_response(handler, job_to_dict(job, conn))


def handle_job_events(handler, job_id, conn):
    from yamibo_mcp.db.repositories.job_events import JobEventsRepository
    events = JobEventsRepository(conn).list(job_id=job_id, limit=200)
    json_response(handler, [event_to_dict(e) for e in events])


def handle_delete_job(handler, conn):
    _jobs_cache.clear()
    body = read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job is None:
        error_response(handler, "Job not found", HTTPStatus.NOT_FOUND)
        return
    if job.status in ("running",):
        error_response(handler, "Cannot delete a running job")
        return
    repo.delete_job(job_id)
    json_response(handler, {"ok": True, "job_id": job_id})


def handle_retry_job(handler, conn):
    _jobs_cache.clear()
    body = read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job.status not in (JobStatus.PARTIAL.value, JobStatus.FAILED.value, JobStatus.INTERRUPTED.value):
        error_response(handler, "Only partial, failed, or interrupted jobs can be retried")
        return
    try:
        next_job = repo.rerun(job.job_id)
    except ValueError as exc:
        error_response(handler, str(exc))
        return
    json_response(handler, {
        "ok": True,
        "job_id": next_job.job_id,
        "source_job_id": job.job_id,
        "status": next_job.status,
    })


def handle_pause_job(handler, conn):
    _jobs_cache.clear()
    body = read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job.status == JobStatus.PAUSED.value:
        json_response(handler, {"ok": True, "job_id": job_id, "status": JobStatus.PAUSED.value})
        return
    if not repo.pause(job_id):
        error_response(handler, "Cannot pause job")
        return
    json_response(handler, {"ok": True, "job_id": job_id, "status": JobStatus.PAUSED.value})


def handle_resume_job(handler, conn):
    _jobs_cache.clear()
    body = read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job.status != JobStatus.PAUSED.value:
        error_response(handler, "Only paused jobs can be resumed")
        return
    if not repo.resume(job_id):
        error_response(handler, "Cannot resume job while it is still owned by a worker")
        return
    json_response(handler, {"ok": True, "job_id": job_id, "status": JobStatus.QUEUED.value})


def handle_job_control(handler, conn, settings: Settings | None = None):
    _jobs_cache.clear()
    body = read_json_body(handler)
    action = body.get("action")
    repo = JobsRepository(conn)
    if action == "pause":
        if settings is not None:
            persist_jobs_enabled(settings, False)
        changed_job_ids = repo.pause_active_jobs()
        json_response(handler, {
            "ok": True,
            "action": "pause",
            "changed_job_ids": changed_job_ids,
            "changed_count": len(changed_job_ids),
            "job_control": {**repo.job_control_summary(), "jobs_enabled": False},
        })
        return
    if action == "resume":
        if settings is not None:
            persist_jobs_enabled(settings, True)
        changed_job_ids = repo.resume_paused_jobs()
        json_response(handler, {
            "ok": True,
            "action": "resume",
            "changed_job_ids": changed_job_ids,
            "changed_count": len(changed_job_ids),
            "job_control": {**repo.job_control_summary(), "jobs_enabled": True},
        })
        return
    error_response(handler, "action must be pause or resume")


def handle_resume_remote_access(handler, conn):
    _jobs_cache.clear()
    json_response(handler, clear_remote_access_pause(conn))


def handle_batch_delete_jobs(handler, conn):
    _jobs_cache.clear()
    body = read_json_body(handler)
    status_filter = body.get("status")
    if not status_filter:
        error_response(handler, "status required")
        return
    if status_filter == "running":
        error_response(handler, "Cannot batch delete running jobs")
        return
    job_ids = JobsRepository(conn).list_ids_by_status(status_filter)
    if not job_ids:
        json_response(handler, {"ok": True, "deleted": 0})
        return
    JobsRepository(conn).delete_jobs(job_ids)
    json_response(handler, {"ok": True, "deleted": len(job_ids)})


def handle_batch_delete_jobs_by_ids(handler, conn):
    _jobs_cache.clear()
    body = read_json_body(handler)
    job_ids = body.get("job_ids", [])
    if not job_ids:
        error_response(handler, "job_ids required")
        return
    running = JobsRepository(conn).list_running_job_ids([str(job_id) for job_id in job_ids])
    if running:
        error_response(handler, "Cannot delete running jobs")
        return
    JobsRepository(conn).delete_jobs(job_ids)
    json_response(handler, {"ok": True, "deleted": len(job_ids)})


def handle_safe_delete_job(handler, conn):
    _jobs_cache.clear()
    body = read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job is None:
        error_response(handler, "Job not found", HTTPStatus.NOT_FOUND)
        return
    if job.status == "running":
        ok = repo.request_cancel(job_id)
        if not ok:
            error_response(handler, "Failed to request cancellation")
            return
        json_response(handler, {"ok": True, "action": "cancel_requested", "job_id": job_id})
    else:
        JobsRepository(conn).delete_job(job_id)
        json_response(handler, {"ok": True, "action": "deleted", "job_id": job_id})
