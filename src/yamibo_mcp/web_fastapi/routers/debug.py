from __future__ import annotations

import platform
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.forums import ForumsRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.db.observability import describe_engine_pool
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings

router = APIRouter(prefix="/api", tags=["debug"])


@router.get("/debug/info")
def debug_info(conn: DatabaseConnection = Depends(get_conn), settings=Depends(get_settings)):
    jobs_repo = JobsRepository(conn)
    thread_count = ThreadsRepository(conn).count_threads()
    series_count = SeriesRepository(conn).count_series()
    job_count = jobs_repo.count()
    event_count = JobEventsRepository(conn).count()
    asset_count = AssetsRepository(conn).count_assets()
    block_count = ContentBlocksRepository(conn).count_blocks()
    forum_count = ForumsRepository(conn).count_forums()
    recent_jobs = jobs_repo.list_recent(limit=10)
    recent_errors = jobs_repo.list_recent_errors(limit=10)
    static_dir = Path(__file__).parent.parent.parent / "web" / "static"
    db_url = getattr(settings, "db_url", None)
    db_host = None
    db_name = None
    if db_url:
        parsed = urlparse(db_url)
        db_host = parsed.hostname
        db_name = parsed.path.lstrip("/") or None
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "db_backend": settings.db_backend,
        "db_host": db_host,
        "db_name": db_name,
        "db_ssl_mode": settings.db_ssl_mode if settings.db_backend == "postgres" else None,
        "data_dir": str(settings.data_dir),
        "db_path": str(settings.db_path),
        "db_pool": describe_engine_pool(getattr(conn, "engine", None)),
        "static_exists": static_dir.exists(),
        "stats": {
            "threads": thread_count, "series": series_count,
            "jobs": job_count, "events": event_count,
            "assets": asset_count, "blocks": block_count,
            "forums": forum_count,
        },
        "recent_jobs": [
            {"job_id": r["job_id"], "type": r["job_type"], "status": r["status"], "created": r["created_at"]}
            for r in recent_jobs
        ],
        "recent_errors": [
            {"job_id": r["job_id"], "code": r["error_code"], "message": r["error_message"], "finished": r["finished_at"]}
            for r in recent_errors
        ],
    }


@router.get("/logs")
def logs(
    limit: int = Query(default=200, ge=1, le=500),
    since: str | None = Query(default=None, description="ISO-8601 timestamp; return entries newer than this value"),
    job_id: str | None = Query(default=None),
    tid: int | None = Query(default=None),
    event_type: str | None = Query(default=None),
    level: str | None = Query(default=None),
    component: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Case-insensitive search across the complete structured entry"),
    errors_only: bool = Query(default=False),
):
    from yamibo_mcp.web_fastapi.log_buffer import get_log_buffer
    buf = get_log_buffer()
    entries = buf.get_recent(
        limit=limit,
        since_ts=since,
        job_id=job_id,
        tid=tid,
        event_type=event_type,
        level=level,
        component=component,
        query=q,
        errors_only=errors_only,
    )
    return {
        "entries": entries,
        "count": len(entries),
        "oldest_ts": entries[0].get("ts") if entries else None,
        "newest_ts": entries[-1].get("ts") if entries else None,
        "applied_filters": {
            key: value
            for key, value in {
                "since": since,
                "job_id": job_id,
                "tid": tid,
                "event_type": event_type,
                "level": level,
                "component": component,
                "q": q,
                "errors_only": errors_only or None,
            }.items()
            if value is not None
        },
    }
