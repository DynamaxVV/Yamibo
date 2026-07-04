from __future__ import annotations

from fastapi import APIRouter, Depends

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.yamibo.anti_bot import get_remote_access_pause_state

router = APIRouter(prefix="/api", tags=["daemon"])


def _compute_operational_mode(conn, settings: Settings) -> str:
    remote_state = get_remote_access_pause_state(conn)
    if remote_state and remote_state.get("active"):
        return "remote_paused"
    if not getattr(settings, "jobs_enabled", True):
        return "paused"
    repo = JobsRepository(conn)
    counts = repo.job_control_summary()
    active_jobs = (
        counts.get("queued", 0) + counts.get("running", 0) + counts.get("retrying", 0)
    )
    if active_jobs > 0:
        return "active"
    return "idle"


@router.get("/daemon/status")
def daemon_status(conn: DatabaseConnection = Depends(get_conn)):
    settings = load_settings()
    repo = JobsRepository(conn)
    counts = repo.job_control_summary()
    workers = repo.list_worker_heartbeats()
    mode = _compute_operational_mode(conn, settings)
    return {
        "operational_mode": mode,
        "jobs_enabled": getattr(settings, "jobs_enabled", True),
        "job_counts": {
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "retrying": counts.get("retrying", 0),
            "interrupted": counts.get("interrupted", 0),
            "paused": counts.get("paused", 0),
        },
        "daemon_alive": len(workers) > 0 or mode not in ("paused", "remote_paused"),
        "worker_count": len(workers),
        "remote_access_paused": mode == "remote_paused",
    }
