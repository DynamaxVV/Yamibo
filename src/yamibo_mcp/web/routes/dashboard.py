from __future__ import annotations

from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.yamibo.anti_bot import get_remote_access_pause_state
from ._helpers import json_response
from ._converters import (
    job_rows_to_dicts, thread_summary_dict, audit_to_dict,
    SYNC_THREAD_LIVE_STATUSES,
)


def handle_dashboard(handler, conn, params, settings=None):
    jobs_repo = JobsRepository(conn)
    threads_repo = ThreadsRepository(conn)
    thread_count = threads_repo.count_threads()
    series_count = SeriesRepository(conn).count_series()
    export_count = threads_repo.count_exported_threads()
    forum_counts = {int(row["forum_id"]): int(row["cnt"]) for row in threads_repo.count_threads_by_forum()}

    recent_limit = int(params.get("limit", ["10"])[0])
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
    recent_threads = [thread_summary_dict(r) for r in threads_repo.list_threads(limit=recent_limit)]
    remote_access_pause = get_remote_access_pause_state(conn)

    json_response(handler, {
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
        "job_control": {**jobs_repo.job_control_summary(), "jobs_enabled": getattr(settings, "jobs_enabled", True)},
    })
