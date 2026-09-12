from __future__ import annotations

from fastapi import APIRouter, Depends

from yamibo_mcp.application.system_queries import build_system_status
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings

router = APIRouter(prefix="/api", tags=["daemon"])


@router.get("/daemon/status")
def daemon_status(
    conn: DatabaseConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    status = build_system_status(conn, settings)
    jobs = status["jobs"]
    daemon = status["daemon"]
    return {
        "operational_mode": daemon["operational_mode"],
        "jobs_enabled": jobs["enabled"],
        "job_counts": jobs["counts"],
        "daemon_alive": daemon["alive"],
        "worker_count": daemon["worker_count"],
        "workers": daemon["workers"],
        "remote_access_paused": status["remote_access"]["paused"],
    }


@router.get("/system/status")
def system_status(
    conn: DatabaseConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    return build_system_status(conn, settings)
