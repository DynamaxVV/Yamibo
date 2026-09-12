from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yamibo_mcp import __version__
from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.time_utils import utc_now_iso
from yamibo_mcp.yamibo.anti_bot import get_remote_access_pause_state


def fresh_worker_heartbeats(conn, settings: Settings) -> list[dict[str, object]]:
    max_age = max(float(getattr(settings, "worker_heartbeat_seconds", 15)) * 3, 30.0)
    now = datetime.now(timezone.utc)
    workers: list[dict[str, object]] = []
    for item in SystemStateRepository(conn).list_json("worker_heartbeat:"):
        value = item["value"]
        if not isinstance(value, dict):
            continue
        heartbeat_at = value.get("heartbeat_at") or item.get("updated_at")
        try:
            timestamp = datetime.fromisoformat(str(heartbeat_at).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        age = max(0.0, (now - timestamp).total_seconds())
        if age > max_age:
            continue
        workers.append(
            {
                "worker_id": value.get("worker_id") or item["key"].split(":", 1)[-1],
                "status": value.get("status", "running"),
                "heartbeat_at": str(heartbeat_at),
                "age_seconds": round(age, 1),
            }
        )
    workers.sort(key=lambda worker: str(worker["heartbeat_at"]), reverse=True)
    return workers


def build_system_status(conn, settings: Settings) -> dict[str, Any]:
    jobs_repo = JobsRepository(conn)
    counts = jobs_repo.job_control_summary()
    remote_state = get_remote_access_pause_state(conn)
    workers = fresh_worker_heartbeats(conn, settings)
    jobs_enabled = bool(getattr(settings, "jobs_enabled", True))
    if remote_state:
        operational_mode = "remote_paused"
    elif not jobs_enabled:
        operational_mode = "paused"
    elif sum(counts.get(name, 0) for name in ("queued", "running", "retrying")):
        operational_mode = "active"
    else:
        operational_mode = "idle"

    return {
        "observed_at": utc_now_iso(),
        "application": {"version": __version__},
        "database": {"backend": settings.db_backend, "ok": True},
        "jobs": {
            "enabled": jobs_enabled,
            "counts": {
                "queued": counts.get("queued", 0),
                "running": counts.get("running", 0),
                "retrying": counts.get("retrying", 0),
                "interrupted": counts.get("interrupted", 0),
                "paused": counts.get("paused", 0),
            },
        },
        "daemon": {
            "alive": bool(workers),
            "operational_mode": operational_mode,
            "worker_count": len(workers),
            "workers": workers,
        },
        "remote_access": {
            "paused": remote_state is not None,
            "state": remote_state,
        },
        "not_checked": [
            "reverse_proxy_auth",
            "backup_restore_drill",
            "external_deployment",
        ],
    }


def read_system_status() -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path, bootstrap=False)
    try:
        data = build_system_status(conn, settings)
    finally:
        conn.close()
    return AgentResult(ok=True, data=data)
