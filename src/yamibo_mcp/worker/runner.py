from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from uuid import uuid4

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.errors import LeaseNotAcquired
from yamibo_mcp.worker.handlers import get_handler
from yamibo_mcp.worker.recovery import recover_expired_jobs

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerResult:
    processed: int


class WorkerRunner:
    def __init__(self, settings: Settings, worker_id: str | None = None):
        self.settings = settings
        self.worker_id = worker_id or settings.worker_id or f"worker_{uuid4().hex[:12]}"

    def run_once(self) -> WorkerResult:
        conn = connect(self.settings.db_path)
        try:
            migrate(conn)
            repo = JobsRepository(conn)
            # 每次循环先做恢复，再抢任务，这样 Worker 崩溃后的 running 任务能重新被消费。
            recover_expired_jobs(repo)
            job = repo.acquire_next(self.worker_id, self.settings.worker_lease_seconds)
            if job is None:
                return WorkerResult(processed=0)
            LOG.info("Acquired job %s (%s)", job.job_id, job.job_type)
            handler = get_handler(job)
            if handler is None:
                repo.fail(job.job_id, "UNKNOWN_JOB_TYPE", f"No handler for job type: {job.job_type}")
                return WorkerResult(processed=1)
            try:
                handler(repo, job, self.worker_id, self.settings.worker_lease_seconds, self.settings)
            except Exception as exc:  # noqa: BLE001 - top-level worker boundary
                LOG.exception("Job %s failed", job.job_id)
                repo.fail(job.job_id, exc.__class__.__name__, str(exc))
            return WorkerResult(processed=1)
        except LeaseNotAcquired:
            return WorkerResult(processed=0)
        finally:
            conn.close()

    def run_forever(self) -> None:
        LOG.info("Starting worker %s", self.worker_id)
        while True:
            result = self.run_once()
            if result.processed == 0:
                time.sleep(self.settings.worker_poll_seconds)
