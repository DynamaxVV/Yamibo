from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Event, Thread
from uuid import uuid4

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.errors import LeaseNotAcquired
from yamibo_mcp.daemon.handlers import get_handler
from yamibo_mcp.daemon.handlers.sync_thread import JobCancelled, JobPaused
from yamibo_mcp.daemon.recovery import recover_expired_jobs

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DaemonResult:
    processed: int


class DaemonRunner:
    def __init__(self, settings: Settings, worker_id: str | None = None):
        self.settings = settings
        self.worker_id = worker_id or settings.worker_id or f"daemon_{uuid4().hex[:12]}"

    def run_once(self) -> DaemonResult:
        conn = connect(self.settings.db_path)
        try:
            migrate(conn)
            repo = JobsRepository(conn)
            recover_expired_jobs(repo)
            job = repo.acquire_next(self.worker_id, self.settings.worker_lease_seconds)
            if job is None:
                return DaemonResult(processed=0)
            LOG.info("Acquired job %s (%s)", job.job_id, job.job_type)
            handler = get_handler(job)
            if handler is None:
                repo.fail(job.job_id, "UNKNOWN_JOB_TYPE", f"No handler for job type: {job.job_type}")
                return DaemonResult(processed=1)
            try:
                handler(repo, job, self.worker_id, self.settings.worker_lease_seconds, self.settings)
            except JobCancelled:
                LOG.info("Job %s was cancelled", job.job_id)
                repo.fail(job.job_id, "CANCELLED", "Job was cancelled by user")
            except JobPaused:
                LOG.info("Job %s was paused", job.job_id)
                repo.finalize_pause(job.job_id)
            except Exception as exc:  # noqa: BLE001 - top-level daemon boundary
                LOG.exception("Job %s failed", job.job_id)
                repo.fail(job.job_id, exc.__class__.__name__, str(exc))
            return DaemonResult(processed=1)
        except LeaseNotAcquired:
            return DaemonResult(processed=0)
        finally:
            conn.close()

    def run_forever(self) -> None:
        worker_parallelism = max(getattr(self.settings, "worker_parallelism", 1), 1)
        if worker_parallelism == 1:
            LOG.info("Starting daemon %s", self.worker_id)
            while True:
                result = self.run_once()
                if result.processed == 0:
                    time.sleep(self.settings.worker_poll_seconds)
            return

        stop_event = Event()
        threads: list[Thread] = []
        LOG.info("Starting daemon %s with %s workers", self.worker_id, worker_parallelism)
        for index in range(worker_parallelism):
            worker = DaemonRunner(self.settings, worker_id=f"{self.worker_id}-{index + 1}")
            thread = Thread(target=worker._run_worker_loop, args=(stop_event,), daemon=True)
            threads.append(thread)
            thread.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_event.set()
            for thread in threads:
                thread.join(timeout=5)

    def _run_worker_loop(self, stop_event: Event) -> None:
        LOG.info("Worker %s started", self.worker_id)
        while not stop_event.is_set():
            result = self.run_once()
            if result.processed == 0:
                stop_event.wait(self.settings.worker_poll_seconds)
