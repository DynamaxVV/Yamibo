from __future__ import annotations

from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobType


def create_update_thread_job(*, tid: int, base_url: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        payload = {"tid": tid}
        if base_url:
            payload["base_url"] = base_url
        existing = repo.find_live_job_for_thread(job_type=JobType.UPDATE_THREAD.value, tid=tid)
        if existing is not None and existing.payload == payload:
            return {"job_id": existing.job_id, "created": False}
        job = repo.create(JobType.UPDATE_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id, "created": True}
    finally:
        conn.close()
