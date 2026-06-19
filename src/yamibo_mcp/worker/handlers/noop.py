from __future__ import annotations

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.config import Settings
from yamibo_mcp.domain.models import Job


def handle_noop(repo: JobsRepository, job: Job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
    # no-op handler 专门用来验证任务底座，不依赖帖子解析或文件写入。
    repo.update_stage(job.job_id, "noop", progress_current=0, progress_total=1)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    repo.update_stage(job.job_id, "noop", progress_current=1, progress_total=1)
    repo.succeed(job.job_id, {"message": "noop completed"})
