from __future__ import annotations

import logging

from yamibo_mcp.db.repositories.jobs import JobsRepository

LOG = logging.getLogger(__name__)


def recover_expired_jobs(repo: JobsRepository) -> int:
    # Step 1 的核心恢复动作：把过期租约的 running 任务重新放回可恢复状态。
    count = repo.mark_expired_running_interrupted()
    if count:
        LOG.info("Marked %s expired running job(s) as interrupted", count)
    return count
