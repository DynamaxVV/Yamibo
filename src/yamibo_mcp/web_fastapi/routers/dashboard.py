from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.web_fastapi.converters import job_rows_to_dicts, thread_summary_dict, audit_to_dict
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.web_fastapi.archive_counts import get_archive_counts
from yamibo_mcp.yamibo.anti_bot import get_remote_access_pause_state
from yamibo_mcp.yamibo.proxy_pool import YAMIBO_HEALTH_CHECK_URL, get_cached_proxy_pool_health

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/proxy-pool/health")
def get_proxy_pool_health(
    refresh: bool = Query(default=False),
    settings=Depends(get_settings),
):
    """Return a cached report; refresh=true forces a new node test."""
    report = get_cached_proxy_pool_health(
        settings,
        test_url=YAMIBO_HEALTH_CHECK_URL,
        force_refresh=refresh,
    )
    report.setdefault("nodes", [])
    return report


@router.get("/dashboard")
def get_dashboard(
    limit: int = Query(default=10),
    conn: DatabaseConnection = Depends(get_conn),
    settings=Depends(get_settings),
):
    jobs_repo = JobsRepository(conn)
    threads_repo = ThreadsRepository(conn)
    archive_counts = get_archive_counts(threads_repo, database_scope=str(settings.db_url or settings.db_path))
    thread_count = archive_counts["thread_count"]
    series_count = SeriesRepository(conn).count_series()
    export_count = archive_counts["export_count"]
    forum_counts = archive_counts["forum_counts"]

    recent_job_rows = jobs_repo.list_recent(limit=10)
    recent_jobs = job_rows_to_dicts(recent_job_rows, conn, include_details=False)
    live_thread_statuses = jobs_repo.list_live_sync_thread_statuses()
    workers = [
        {
            "worker_id": row["worker_id"],
            "running_jobs": row["running_jobs"],
            "seen_jobs": row["seen_jobs"],
            "latest_heartbeat_at": row["latest_heartbeat_at"],
        }
        for row in jobs_repo.list_worker_heartbeats()
    ]
    audits = [audit_to_dict(r, conn) for r in AuditEventsRepository(conn).list_recent(limit=8)]
    recent_threads = [thread_summary_dict(r) for r in threads_repo.list_threads(limit=limit)]
    remote_access_pause = get_remote_access_pause_state(conn)
    # Reload config so jobs_enabled reflects in-flight writes by /api/jobs/control.
    # The cached settings on app.state is frozen at startup.
    fresh_settings = load_settings()

    return {
        "thread_count": thread_count,
        "series_count": series_count,
        "export_count": export_count,
        "forum_counts": forum_counts,
        "recent_jobs": recent_jobs,
        "live_thread_statuses": live_thread_statuses,
        "workers": workers,
        "recent_audits": audits,
        "recent_threads": recent_threads,
        "remote_access_pause": remote_access_pause,
        "job_control": {**jobs_repo.job_control_summary(), "jobs_enabled": getattr(fresh_settings, "jobs_enabled", True)},
    }
