from __future__ import annotations

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.yamibo.anti_bot import get_remote_access_pause_state
from ._helpers import json_response


def compute_operational_mode(conn, settings: Settings) -> str:
    """Derive the daemon's operational mode from live DB state and config.

    Returns one of: ``active``, ``idle``, ``paused``, ``remote_paused``.
    There is no ``stopped`` state — when the daemon is truly down there are
    no heartbeats at all, which is indistinguishable from an idle daemon with
    an empty queue.  The workers panel on the dashboard is the authoritative
    source for daemon-aliveness.
    """
    # Anti-bot pause overrides everything.
    remote_state = get_remote_access_pause_state(conn)
    if remote_state and remote_state.get("active"):
        return "remote_paused"

    # User-initiated global pause — show regardless of daemon aliveness.
    if not getattr(settings, "jobs_enabled", True):
        return "paused"

    repo = JobsRepository(conn)
    counts = repo.job_control_summary()
    active_jobs = (
        counts.get("queued", 0)
        + counts.get("running", 0)
        + counts.get("retrying", 0)
    )
    if active_jobs > 0:
        return "active"

    return "idle"


def handle_daemon_status(handler, conn, _params, _settings: Settings) -> None:
    """GET /api/daemon/status — lightweight operational-mode poll endpoint.

    Reloads settings on every call so that config writes (e.g. from
    /api/jobs/control) are visible without a daemon restart.
    """
    settings = load_settings()
    repo = JobsRepository(conn)
    counts = repo.job_control_summary()
    workers = repo.list_worker_heartbeats()
    mode = compute_operational_mode(conn, settings)
    json_response(handler, {
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
    })
