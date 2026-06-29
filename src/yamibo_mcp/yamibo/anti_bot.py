from __future__ import annotations

import urllib.error
from typing import Any

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.errors import RemoteAccessPausedError, RemoteFetchError
from yamibo_mcp.time_utils import utc_now_iso


REMOTE_ACCESS_PAUSE_KEY = "remote_access_pause"
_PAUSED_REMOTE_JOB_TYPES = (
    JobType.SYNC_THREAD.value,
    JobType.UPDATE_THREAD.value,
)
_LIVE_PAUSABLE_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.INTERRUPTED.value,
}


def is_http_444_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 444
    if isinstance(exc, RemoteFetchError):
        details = getattr(exc, "details", None)
        if isinstance(details, dict) and details.get("status_code") == 444:
            return True
        text = str(exc).lower()
        return "http error 444" in text or "status 444" in text
    return False


def get_remote_access_pause_state(conn) -> dict[str, Any] | None:
    state = SystemStateRepository(conn).get_json(REMOTE_ACCESS_PAUSE_KEY)
    if not isinstance(state, dict) or not state.get("active"):
        return None
    return state


def ensure_remote_access_allowed(conn) -> None:
    state = get_remote_access_pause_state(conn)
    if state is None:
        return
    triggered_at = state.get("triggered_at") or "unknown"
    source = state.get("source") or "unknown"
    raise RemoteAccessPausedError(
        f"remote access paused after HTTP 444 anti-bot detection at {triggered_at} ({source})",
        details=state,
    )


def activate_remote_access_pause(
    conn,
    *,
    source: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jobs_repo = JobsRepository(conn)
    paused_job_ids: list[str] = []
    for job in jobs_repo.list(limit=None):
        if job.job_type not in _PAUSED_REMOTE_JOB_TYPES or job.status not in _LIVE_PAUSABLE_JOB_STATUSES:
            continue
        if jobs_repo.pause(job.job_id):
            paused_job_ids.append(job.job_id)
    state = {
        "active": True,
        "reason": "http_444",
        "message": message,
        "source": source,
        "triggered_at": utc_now_iso(),
        "paused_job_ids": paused_job_ids,
        "paused_job_count": len(paused_job_ids),
        "job_types": list(_PAUSED_REMOTE_JOB_TYPES),
        "context": context or {},
    }
    SystemStateRepository(conn).set_json(REMOTE_ACCESS_PAUSE_KEY, state)
    return state


def clear_remote_access_pause(conn, *, resume_jobs: bool = True) -> dict[str, Any]:
    jobs_repo = JobsRepository(conn)
    resumed_job_ids: list[str] = []
    if resume_jobs:
        for job in jobs_repo.list(limit=None, status=JobStatus.PAUSED):
            if job.job_type not in _PAUSED_REMOTE_JOB_TYPES:
                continue
            if jobs_repo.resume(job.job_id):
                resumed_job_ids.append(job.job_id)
    SystemStateRepository(conn).delete(REMOTE_ACCESS_PAUSE_KEY)
    return {
        "ok": True,
        "resumed_job_ids": resumed_job_ids,
        "resumed_job_count": len(resumed_job_ids),
    }
