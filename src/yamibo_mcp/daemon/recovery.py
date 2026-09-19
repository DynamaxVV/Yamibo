from __future__ import annotations

import logging

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.structured_logging import emit

LOG = logging.getLogger(__name__)


def recover_expired_jobs(repo: JobsRepository) -> int:
    # 过期任务会按重试预算进入 interrupted 或 failed，避免 stale-job loop。
    recovered_count = repo.mark_expired_running_interrupted()
    if recovered_count:
        LOG.info("Recovered %s expired running job(s)", recovered_count)
        emit(LOG, logging.INFO, "maintenance.recovery_finished",
             f"Recovered {recovered_count} expired running job(s)",
             result="success", status="ok",
             tags=["maintenance", "recovery"])
    released_count = repo.release_expired_paused_jobs()
    if released_count:
        LOG.info("Released %s expired paused job(s)", released_count)
        emit(LOG, logging.INFO, "maintenance.recovery_finished",
             f"Released {released_count} expired paused job(s)",
             result="success", status="ok",
             tags=["maintenance", "recovery"])
    return recovered_count + released_count
