from __future__ import annotations

from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.schemas import job_status_payload


def get_job_status_payload(job_id: str) -> dict[str, Any]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        job = JobsRepository(conn).get(job_id)
        return job_status_payload(job)
    finally:
        conn.close()
