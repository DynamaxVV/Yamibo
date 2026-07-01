from __future__ import annotations

import logging

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.structured_logging import emit

LOG = logging.getLogger(__name__)


def recover_expired_jobs(repo: JobsRepository) -> int:
    # Step 1 的核心恢复动作：把过期租约的 running 任务重新放回可恢复状态。
    interrupted_count = repo.mark_expired_running_interrupted()
    if interrupted_count:
        LOG.info("Marked %s expired running job(s) as interrupted", interrupted_count)
        emit(LOG, logging.INFO, "maintenance.recovery_finished",
             f"Marked {interrupted_count} expired running job(s) as interrupted",
             result="success", status="ok",
             tags=["maintenance", "recovery"])
    released_count = repo.release_expired_paused_jobs()
    if released_count:
        LOG.info("Released %s expired paused job(s)", released_count)
        emit(LOG, logging.INFO, "maintenance.recovery_finished",
             f"Released {released_count} expired paused job(s)",
             result="success", status="ok",
             tags=["maintenance", "recovery"])
    return interrupted_count + released_count
