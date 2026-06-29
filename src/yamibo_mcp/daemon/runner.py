from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Event, Thread
from uuid import uuid4

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.errors import LeaseNotAcquired, RemoteFetchError, ThreadPermissionRequiredError
from yamibo_mcp.daemon.handlers import get_handler
from yamibo_mcp.daemon.handlers.sync_thread import JobCancelled, JobPaused
from yamibo_mcp.daemon.recovery import recover_expired_jobs
from yamibo_mcp.maintenance.forum_sizes import FORUM_SIZE_CACHE_REFRESH_SECONDS, refresh_forum_size_cache
from yamibo_mcp.yamibo.anti_bot import activate_remote_access_pause, is_http_444_error

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
            except ThreadPermissionRequiredError as exc:
                LOG.warning("Job %s requires higher read permission: %s", job.job_id, exc)
                repo.fail(job.job_id, exc.__class__.__name__, str(exc))
            except Exception as exc:  # noqa: BLE001 - top-level daemon boundary
                LOG.exception("Job %s failed", job.job_id)
                conn.rollback()
                if isinstance(exc, RemoteFetchError) and is_http_444_error(exc):
                    state = activate_remote_access_pause(
                        conn,
                        source=f"daemon:{job.job_type}:{job.job_id}",
                        message="Yamibo returned HTTP 444. Remote archive/update access has been paused.",
                        context={"job_id": job.job_id, "job_type": job.job_type, "tid": job.tid, "remote_fetch": exc.details},
                    )
                    repo.finalize_pause(job.job_id)
                    LOG.warning("Paused remote archive/update jobs after HTTP 444: %s", state)
                    return DaemonResult(processed=1)
                artifacts = {
                    "failure_context": {
                        "exception_type": exc.__class__.__name__,
                        "message": str(exc),
                    }
                }
                if isinstance(exc, RemoteFetchError) and getattr(exc, "details", None):
                    artifacts["failure_context"]["remote_fetch"] = exc.details
                if (
                    isinstance(exc, RemoteFetchError)
                    and isinstance(getattr(exc, "details", None), dict)
                    and exc.details.get("retryable")
                    and repo.retry_later(
                        job.job_id,
                        error_code=exc.__class__.__name__,
                        error_message=str(exc),
                        artifacts=artifacts,
                    )
                ):
                    LOG.warning("Job %s moved to retrying after transient remote fetch failure", job.job_id)
                else:
                    repo.fail(job.job_id, exc.__class__.__name__, str(exc), artifacts=artifacts)
            return DaemonResult(processed=1)
        except LeaseNotAcquired:
            return DaemonResult(processed=0)
        finally:
            conn.close()

    def run_forever(self) -> None:
        worker_parallelism = max(getattr(self.settings, "worker_parallelism", 1), 1)
        stop_event = Event()
        cache_thread = Thread(target=self._run_forum_size_cache_loop, args=(stop_event,), daemon=True)
        cache_thread.start()
        if worker_parallelism == 1:
            LOG.info("Starting daemon %s", self.worker_id)
            try:
                while not stop_event.is_set():
                    result = self.run_once()
                    if result.processed == 0:
                        stop_event.wait(self.settings.worker_poll_seconds)
            except KeyboardInterrupt:
                stop_event.set()
            finally:
                cache_thread.join(timeout=5)
            return

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
            cache_thread.join(timeout=5)

    def _run_worker_loop(self, stop_event: Event) -> None:
        LOG.info("Worker %s started", self.worker_id)
        try:
            while not stop_event.is_set():
                try:
                    result = self.run_once()
                except Exception:  # noqa: BLE001 - keep worker alive and surface the failure
                    LOG.exception("Worker %s crashed outside job handler loop", self.worker_id)
                    if stop_event.wait(self.settings.worker_poll_seconds):
                        break
                    continue
                if result.processed == 0:
                    stop_event.wait(self.settings.worker_poll_seconds)
        finally:
            LOG.warning("Worker %s stopped", self.worker_id)

    def _run_forum_size_cache_loop(self, stop_event: Event) -> None:
        LOG.info("Forum size cache refresher started")
        while not stop_event.is_set():
            try:
                refresh_forum_size_cache(self.settings)
            except Exception:  # noqa: BLE001 - background maintenance should not stop the daemon
                LOG.exception("Failed to refresh forum size cache")
            if stop_event.wait(FORUM_SIZE_CACHE_REFRESH_SECONDS):
                break
