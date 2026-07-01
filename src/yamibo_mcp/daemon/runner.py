from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from uuid import uuid4

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.context import LogContext, new_trace_id
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.errors import LeaseNotAcquired, RemoteFetchError, ThreadPermissionRequiredError, classify_error
from yamibo_mcp.daemon.handlers import get_handler
from yamibo_mcp.daemon.handlers.sync_thread import JobCancelled, JobPaused
from yamibo_mcp.daemon.recovery import recover_expired_jobs
from yamibo_mcp.maintenance.forum_sizes import FORUM_SIZE_CACHE_REFRESH_SECONDS, refresh_forum_size_cache
from yamibo_mcp.structured_logging import emit
from yamibo_mcp.yamibo.anti_bot import activate_remote_access_pause, is_http_429_error, is_http_444_error
from yamibo_mcp.yamibo.proxy_pool import clear_proxy_cache

LOG = logging.getLogger(__name__)

# Graduated 444 response: track consecutive 444s within a time window;
# persist to system_state for crash-survival. Only escalate to global pause
# after hitting threshold.
_444_KEY = "anti_bot_444_events"
_444_EVENTS: list[float] = []
_444_LOCK = Lock()
_444_THRESHOLD = 3
_444_WINDOW_SECONDS = 300  # 5 minutes


def _restore_444_events(conn) -> None:
    """Restore persisted 444 events from system_state on daemon startup."""
    state = SystemStateRepository(conn).get_json(_444_KEY)
    if not isinstance(state, dict):
        return
    timestamps = state.get("timestamps", [])
    if not isinstance(timestamps, list):
        return
    now = time.monotonic()
    with _444_LOCK:
        _444_EVENTS[:] = [float(t) for t in timestamps if isinstance(t, (int, float)) and now - float(t) < _444_WINDOW_SECONDS]


def _record_444(conn) -> bool:
    """Record a 444 event. Persists to system_state. Returns True if threshold exceeded."""
    now = time.monotonic()
    with _444_LOCK:
        _444_EVENTS[:] = [t for t in _444_EVENTS if now - t < _444_WINDOW_SECONDS]
        _444_EVENTS.append(now)
        exceeded = len(_444_EVENTS) >= _444_THRESHOLD
        try:
            SystemStateRepository(conn).set_json(_444_KEY, {"timestamps": _444_EVENTS, "threshold": _444_THRESHOLD, "window_seconds": _444_WINDOW_SECONDS})
        except Exception:
            pass  # persistence is best-effort; don't break the handler
        return exceeded


def _is_soft_block_error(exc: RemoteFetchError) -> bool:
    """Check whether a RemoteFetchError was caused by a CF challenge / CAPTCHA."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        msg = str(exc).lower()
        return "soft block" in msg or "cf challenge" in msg or "captcha" in msg
    msg = str(exc).lower()
    return "soft block" in msg or "cf challenge" in msg or "captcha" in msg


@dataclass(frozen=True)
class DaemonResult:
    processed: int


class DaemonRunner:
    def __init__(self, settings: Settings, worker_id: str | None = None):
        self.settings = settings
        self.worker_id = worker_id or settings.worker_id or f"daemon_{uuid4().hex[:12]}"

    def _current_settings(self) -> Settings:
        if hasattr(self.settings, "config_path"):
            return load_settings()
        return self.settings

    def _worker_poll_seconds(self) -> float:
        return float(getattr(self._current_settings(), "worker_poll_seconds", self.settings.worker_poll_seconds))

    def run_once(self) -> DaemonResult:
        settings = self._current_settings()
        if not getattr(settings, "jobs_enabled", True):
            return DaemonResult(processed=0)
        conn = connect(settings.db_path)
        try:
            repo = JobsRepository(conn)
            recover_expired_jobs(repo)
            _restore_444_events(conn)
            job = repo.acquire_next(self.worker_id, settings.worker_lease_seconds)
            if job is None:
                return DaemonResult(processed=0)
            LOG.info("Acquired job %s (%s)", job.job_id, job.job_type)
            with LogContext(
                trace_id=new_trace_id(),
                job_id=job.job_id,
                job_type=job.job_type,
                worker_id=self.worker_id,
                tid=job.tid,
            ):
                handler = get_handler(job)
                if handler is None:
                    repo.fail(job.job_id, "UNKNOWN_JOB_TYPE", f"No handler for job type: {job.job_type}")
                    return DaemonResult(processed=1)
                try:
                    handler(repo, job, self.worker_id, settings.worker_lease_seconds, settings)
                except JobCancelled:
                    LOG.info("Job %s was cancelled", job.job_id)
                    repo.fail(job.job_id, "CANCELLED", "Job was cancelled by user")
                except JobPaused:
                    LOG.info("Job %s was paused", job.job_id)
                    repo.finalize_pause(job.job_id)
                except ThreadPermissionRequiredError as exc:
                    LOG.warning("Job %s requires higher read permission: %s", job.job_id, exc)
                    repo.fail(job.job_id, classify_error(exc), str(exc))
                except Exception as exc:  # noqa: BLE001 - top-level daemon boundary
                    LOG.exception("Job %s failed", job.job_id)
                    conn.rollback()

                    # 反爬拦截（444 / soft block）：清除代理缓存后重试，
                    # 让下一次 acquire 能选到不同的 IP 节点。
                    is_anti_bot = (
                        isinstance(exc, RemoteFetchError)
                        and (is_http_444_error(exc) or _is_soft_block_error(exc))
                    )
                    if is_anti_bot:
                        cleared = clear_proxy_cache()
                        if cleared:
                            LOG.info(
                                "Cleared %d proxy cache entries after anti-bot block on job %s",
                                cleared,
                                job.job_id,
                            )

                    if isinstance(exc, RemoteFetchError) and is_http_444_error(exc):
                        if _record_444(conn):
                            state = activate_remote_access_pause(
                                conn,
                                source=f"daemon:{job.job_type}:{job.job_id}",
                                message=f"Yamibo returned HTTP 444 {_444_THRESHOLD} times within {_444_WINDOW_SECONDS}s. Remote access paused.",
                                context={"job_id": job.job_id, "job_type": job.job_type, "tid": job.tid, "remote_fetch": exc.details},
                            )
                            repo.finalize_pause(job.job_id)
                            LOG.warning("Paused remote archive/update jobs after %d consecutive HTTP 444s: %s", _444_THRESHOLD, state)
                        else:
                            repo.retry_later(
                                job.job_id,
                                error_code="HTTP_444",
                                error_message=f"HTTP 444 from {exc.details.get('url', 'unknown')}; proxy cache cleared, will retry with different node",
                            )
                            LOG.warning("HTTP 444 on job %s — will retry with different proxy", job.job_id)
                        return DaemonResult(processed=1)
                    if isinstance(exc, RemoteFetchError) and _is_soft_block_error(exc):
                        repo.retry_later(
                            job.job_id,
                            error_code="REMOTE_SOFT_BLOCK",
                            error_message=f"Soft block / CF challenge from {exc.details.get('url', 'unknown') if isinstance(getattr(exc, 'details', None), dict) else 'unknown'}; proxy cache cleared, will retry with different node",
                        )
                        LOG.warning("Soft block on job %s — will retry with different proxy", job.job_id)
                        return DaemonResult(processed=1)
                    if isinstance(exc, RemoteFetchError) and is_http_429_error(exc):
                        LOG.warning("HTTP 429 rate limit on job %s, retrying later", job.job_id)
                        repo.retry_later(job.job_id, "HTTP_429", str(exc))
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
                            error_code=classify_error(exc),
                            error_message=str(exc),
                            artifacts=artifacts,
                        )
                    ):
                        LOG.warning("Job %s moved to retrying after transient remote fetch failure", job.job_id)
                    else:
                        repo.fail(job.job_id, classify_error(exc), str(exc), artifacts=artifacts)
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
            emit(LOG, logging.INFO, "daemon.started", f"Daemon {self.worker_id} started",
                 result="success", status="running")
            try:
                while not stop_event.is_set():
                    result = self.run_once()
                    if result.processed == 0:
                        stop_event.wait(self._worker_poll_seconds())
            except KeyboardInterrupt:
                stop_event.set()
            finally:
                cache_thread.join(timeout=5)
            emit(LOG, logging.INFO, "daemon.stopped", f"Daemon {self.worker_id} stopped",
                 result="success", status="stopped")
            return

        threads: list[Thread] = []
        LOG.info("Starting daemon %s with %s workers", self.worker_id, worker_parallelism)
        emit(LOG, logging.INFO, "daemon.started", f"Daemon {self.worker_id} started with {worker_parallelism} workers",
             result="success", status="running")
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
        emit(LOG, logging.INFO, "daemon.stopped", f"Daemon {self.worker_id} stopped",
             result="success", status="stopped")
    def _run_worker_loop(self, stop_event: Event) -> None:
        LOG.info("Worker %s started", self.worker_id)
        emit(LOG, logging.INFO, "daemon.worker_started", f"Worker {self.worker_id} started",
             result="success", status="running")
        try:
            while not stop_event.is_set():
                try:
                    result = self.run_once()
                except Exception:  # noqa: BLE001 - keep worker alive and surface the failure
                    LOG.exception("Worker %s crashed outside job handler loop", self.worker_id)
                    emit(LOG, logging.ERROR, "daemon.worker_crashed", f"Worker {self.worker_id} crashed outside job handler loop",
                         result="failure", status="crashed")
                    if stop_event.wait(self._worker_poll_seconds()):
                        break
                    continue
                if result.processed == 0:
                    stop_event.wait(self._worker_poll_seconds())
        finally:
            LOG.warning("Worker %s stopped", self.worker_id)
            emit(LOG, logging.WARNING, "daemon.worker_stopped", f"Worker {self.worker_id} stopped",
                 result="success", status="stopped")

    def _run_forum_size_cache_loop(self, stop_event: Event) -> None:
        LOG.info("Forum size cache refresher started")
        emit(LOG, logging.INFO, "maintenance.cache_refresh_started",
             "Forum size cache refresher started",
             result="success", status="running",
             tags=["maintenance", "cache"])
        while not stop_event.is_set():
            try:
                refresh_forum_size_cache(self.settings)
            except Exception:  # noqa: BLE001 - background maintenance should not stop the daemon
                LOG.exception("Failed to refresh forum size cache")
                emit(LOG, logging.ERROR, "maintenance.cache_refresh_failed",
                     "Failed to refresh forum size cache",
                     result="failure", status="error",
                     tags=["maintenance", "cache"])
            if stop_event.wait(FORUM_SIZE_CACHE_REFRESH_SECONDS):
                break
