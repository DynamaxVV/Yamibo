from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from uuid import uuid4

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.context import LogContext, new_trace_id
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.errors import LeaseNotAcquired, RemoteFetchError, RemoteMaintenanceError, ThreadPermissionRequiredError, UnexpectedPageError, classify_error
from yamibo_mcp.daemon.handlers import get_handler
from yamibo_mcp.daemon.handlers.sync_thread import JobCancelled, JobPaused
from yamibo_mcp.daemon.image_backfill_scheduler import maybe_enqueue_image_backfill_dry_run
from yamibo_mcp.daemon.daily_sign_in_scheduler import maybe_enqueue_daily_sign_ins
from yamibo_mcp.daemon.recovery import recover_expired_jobs
from yamibo_mcp.maintenance.forum_sizes import FORUM_SIZE_CACHE_REFRESH_SECONDS, refresh_forum_size_cache
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.staging import cleanup_stale_staging
from yamibo_mcp.structured_logging import emit
from yamibo_mcp.yamibo.anti_bot import (
    REMOTE_JOB_TYPES,
    activate_remote_access_pause,
    get_remote_access_pause_state,
    handle_http_444,
    is_http_429_error,
    is_http_444_error,
    probe_remote_access,
    record_remote_access_probe_failure,
    record_remote_access_probe_success,
    should_probe_maintenance,
    should_probe_remote_access,
    probe_maintenance,
    get_maintenance_pause_state,
    activate_maintenance_pause,
    record_maintenance_probe_success,
    record_maintenance_probe_failure,
    _restore_444_events,
)
from yamibo_mcp.yamibo.proxy_pool import clear_proxy_cache, get_job_node, bump_retry_hint, record_node_outcome
from yamibo_mcp.yamibo.account_pool import record_account_outcome
from yamibo_mcp.daemon.remote_attempt import outcome_for_error, sanitize_remote_details, now_iso
from yamibo_mcp.time_utils import utc_now_iso

LOG = logging.getLogger(__name__)
_DAILY_SIGN_IN_CHECK_INTERVAL_SECONDS = 60.0 * 60.0
_DAILY_SIGN_IN_SCHEDULER_LOCK = Lock()
_last_daily_sign_in_check: float | None = None


def _maybe_schedule_daily_sign_ins(repo: JobsRepository, settings: Settings) -> None:
    """Throttle the local-account check independently from worker job polling."""
    global _last_daily_sign_in_check
    if not _DAILY_SIGN_IN_SCHEDULER_LOCK.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if (
            _last_daily_sign_in_check is not None
            and now - _last_daily_sign_in_check < _DAILY_SIGN_IN_CHECK_INTERVAL_SECONDS
        ):
            return
        maybe_enqueue_daily_sign_ins(repo, settings)
        _last_daily_sign_in_check = now
    finally:
        _DAILY_SIGN_IN_SCHEDULER_LOCK.release()


def _is_soft_block_error(exc: RemoteFetchError) -> bool:
    """Check whether a RemoteFetchError was caused by a CF challenge / CAPTCHA."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        msg = str(exc).lower()
        return "soft block" in msg or "cf challenge" in msg or "captcha" in msg
    msg = str(exc).lower()
    return "soft block" in msg or "cf challenge" in msg or "captcha" in msg


def _remote_attempt_account(repo: JobsRepository, job_id: str) -> str | None:
    """Read the latest account id after a handler records its attempt context."""
    try:
        current = repo.get(job_id)
    except Exception:  # noqa: BLE001 - diagnostics must not mask the job outcome
        return None
    artifacts = current.artifacts if isinstance(current.artifacts, dict) else {}
    attempt = artifacts.get("remote_attempt")
    account_id = attempt.get("account_id") if isinstance(attempt, dict) else None
    return str(account_id) if account_id else None


def _record_account_feedback(repo: JobsRepository, job_id: str, outcome: str) -> None:
    record_account_outcome(_remote_attempt_account(repo, job_id), outcome)


def _current_remote_attempt(
    repo: JobsRepository,
    job_id: str,
    *,
    fallback_artifacts: object = None,
) -> dict:
    """Read the latest attempt before terminal/retry persistence.

    Handlers record proxy/account selection in their own committed transaction.
    The acquired Job object is therefore only a snapshot and may be stale by
    the time the exception path writes the final outcome.
    """
    try:
        current = repo.get(job_id)
    except Exception:  # noqa: BLE001 - diagnostics must not mask the job outcome
        current = None
    candidates = [
        current.artifacts if current is not None else None,
        fallback_artifacts,
    ]
    for artifacts in candidates:
        if not isinstance(artifacts, dict):
            continue
        attempt = artifacts.get("remote_attempt")
        if isinstance(attempt, dict):
            return dict(attempt)
    return {}


def _retry_or_fail(
    repo: JobsRepository,
    job_id: str,
    *,
    error_code: str,
    error_message: str,
    artifacts: dict,
    delay_seconds: int,
) -> bool:
    """Schedule a retry, or close the running job when its retry budget is spent."""
    scheduled = repo.retry_later(
        job_id,
        error_code=error_code,
        error_message=error_message,
        artifacts=artifacts,
        delay_seconds=delay_seconds,
    )
    if not scheduled:
        repo.fail(job_id, error_code, error_message, artifacts=artifacts)
    return scheduled


def _jittered_retry_delay(retry_count: int, *, base_seconds: int = 10, cap_seconds: int = 60) -> int:
    """Return bounded exponential backoff with small jitter to desynchronise retries."""
    target = min(cap_seconds, base_seconds * (2 ** max(retry_count, 0)))
    jitter = max(1, target // 5)
    lower = max(1, target - jitter)
    upper = min(cap_seconds, target + jitter)
    return random.randint(lower, upper)


def _effective_job_lease_seconds(settings) -> int:
    """Keep the initial lease alive through one bounded remote/image stage.

    Handlers may extend the lease again for a particular stage, but the first
    remote request used to start with the raw short worker lease.  A slow
    request could therefore be recovered and reclaimed before the handler's
    next main-thread heartbeat.
    """
    configured = int(getattr(settings, "worker_lease_seconds", 60))
    request_timeout = float(getattr(settings, "request_timeout_seconds", 30.0))
    image_timeout = float(getattr(settings, "image_download_timeout_seconds", 0.0))
    return max(
        configured,
        int(max(request_timeout * 4.0, 120.0)),
        int(max(image_timeout * 3.0, 120.0)),
    )


@dataclass(frozen=True)
class DaemonResult:
    processed: int


class DaemonRunner:
    def __init__(self, settings: Settings, worker_id: str | None = None):
        self.settings = settings
        self.worker_id = worker_id or settings.worker_id or f"daemon_{uuid4().hex[:12]}"
        self._last_worker_heartbeat = 0.0

    def _current_settings(self) -> Settings:
        if hasattr(self.settings, "config_path"):
            return load_settings()
        return self.settings

    def _worker_poll_seconds(self) -> float:
        return float(getattr(self._current_settings(), "worker_poll_seconds", self.settings.worker_poll_seconds))

    def run_once(self) -> DaemonResult:
        settings = self._current_settings()
        conn = connect(settings.db_path, pool_role="daemon")
        job = None
        try:
            self._publish_worker_heartbeat(conn, settings)
            if not getattr(settings, "jobs_enabled", True):
                return DaemonResult(processed=0)
            repo = JobsRepository(conn, owner_id=self.worker_id)
            recover_expired_jobs(repo)
            _restore_444_events(conn)
            try:
                _maybe_schedule_daily_sign_ins(repo, settings)
            except Exception:
                LOG.exception("Daily sign-in scheduler failed")

            # 维护恢复探测：每 10 分钟用一次轻量请求检查维护是否结束。
            maintenance_state = get_maintenance_pause_state(conn)
            if maintenance_state is not None and should_probe_maintenance(conn):
                LOG.info("Running maintenance recovery probe...")
                conn.commit()
                try:
                    if probe_maintenance(
                        cookie_file=str(settings.cookie_file),
                        settings=settings,
                    ):
                        result = record_maintenance_probe_success(conn)
                        maintenance_state = None
                        LOG.info(
                            "Maintenance over, resumed %s job(s)",
                            result.get("resumed_job_count", 0),
                        )
                    else:
                        record_maintenance_probe_failure(conn)
                        LOG.info("Maintenance still active, will probe again later")
                except Exception:
                    LOG.exception("Maintenance probe failed")
                    record_maintenance_probe_failure(conn)

            # 444 暂停恢复探测：每 10 分钟探一次反爬是否解除，确认正常页面才恢复。
            remote_access_state = get_remote_access_pause_state(conn)
            if remote_access_state is not None and should_probe_remote_access(conn):
                LOG.info("Running HTTP 444 recovery probe...")
                conn.commit()
                try:
                    if probe_remote_access(
                        cookie_file=str(settings.cookie_file),
                        settings=settings,
                    ):
                        result = record_remote_access_probe_success(conn)
                        LOG.info(
                            "HTTP 444 pause lifted, resumed %s job(s)",
                            result.get("resumed_job_count", 0),
                        )
                    else:
                        record_remote_access_probe_failure(conn)
                        LOG.info("HTTP 444 still active, will probe again later")
                except Exception:
                    LOG.exception("HTTP 444 probe failed")
                    record_remote_access_probe_failure(conn)

            # 维护期间：跳过自动任务，但手动恢复的任务可以执行（作为维护探测）。
            lease_seconds = _effective_job_lease_seconds(settings)
            job = repo.acquire_next(self.worker_id, lease_seconds)
            if job is None:
                # 空闲时尝试入队闲时任务（image_backfill）
                if maintenance_state is None and remote_access_state is None:
                    try:
                        maybe_enqueue_image_backfill_dry_run(repo, settings)
                    except Exception:
                        LOG.debug("Image backfill scheduler failed", exc_info=True)
                return DaemonResult(processed=0)
            in_maintenance = maintenance_state is not None
            if in_maintenance and job.status not in (JobStatus.QUEUED.value,):
                return DaemonResult(processed=0)
            LOG.info("Acquired job %s (%s)", job.job_id, job.job_type)
            conn.commit()
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
                    handler(repo, job, self.worker_id, lease_seconds, settings)
                    record_node_outcome(get_job_node(job.job_id), "success")
                    _record_account_feedback(repo, job.job_id, "success")
                    # 远程任务成功说明维护已结束。
                    if in_maintenance and job.job_type in REMOTE_JOB_TYPES:
                        result = record_maintenance_probe_success(conn)
                        LOG.info(
                            "Job %s succeeded during maintenance window — clearing maintenance pause, resumed %s job(s)",
                            job.job_id,
                            result.get("resumed_job_count", 0),
                        )
                except LeaseNotAcquired as exc:
                    # A recovery pass may have transferred this job to a new
                    # worker while this handler was blocked in I/O.  The
                    # repository fences every subsequent write, so the stale
                    # handler must simply stop without recording an outcome.
                    LOG.warning("Job %s lost its lease; stale worker is stopping: %s", job.job_id, exc)
                    return DaemonResult(processed=0)
                except JobCancelled:
                    LOG.info("Job %s was cancelled", job.job_id)
                    repo.fail(job.job_id, "CANCELLED", "Job was cancelled by user")
                except JobPaused:
                    LOG.info("Job %s was paused", job.job_id)
                    repo.finalize_pause(job.job_id)
                except ThreadPermissionRequiredError as exc:
                    LOG.warning("Job %s requires higher read permission: %s", job.job_id, exc)
                    _record_account_feedback(repo, job.job_id, "permission_required")
                    attempt = {**_current_remote_attempt(repo, job.job_id, fallback_artifacts=job.artifacts),
                               "finished_at": now_iso(), "outcome": "permission_required",
                               "node": get_job_node(job.job_id)}
                    repo.fail(job.job_id, classify_error(exc), str(exc), artifacts={"remote_attempt": attempt})
                except Exception as exc:  # noqa: BLE001 - top-level daemon boundary
                    LOG.exception("Job %s failed", job.job_id)
                    conn.rollback()

                    # 论坛维护：激活全局暂停，所有远程任务进入 paused 状态。
                    if isinstance(exc, RemoteMaintenanceError):
                        state = activate_maintenance_pause(
                            conn,
                            source=f"daemon:{job.job_type}:{job.job_id}",
                            context={"job_id": job.job_id, "job_type": job.job_type, "tid": job.tid},
                        )
                        repo.finalize_pause(job.job_id)
                        LOG.warning(
                            "Forum maintenance detected, paused %s remote job(s): %s",
                            state.get("paused_job_count", 0),
                            state,
                        )
                        return DaemonResult(processed=1)

                    # HTTP 444：节点级黑名单优先，全部节点都 444 才全局暂停。
                    if isinstance(exc, RemoteFetchError) and is_http_444_error(exc):
                        node = get_job_node(job.job_id)
                        paused = handle_http_444(
                            conn,
                            source=f"daemon:{job.job_type}:{job.job_id}",
                            exc=exc,
                            context={"job_id": job.job_id, "job_type": job.job_type, "tid": job.tid},
                            node=node,
                            settings=settings,
                        )
                        if paused:
                            repo.finalize_pause(job.job_id)
                        else:
                            bump_retry_hint(job.job_id)
                            record_node_outcome(node, "http_444")
                            attempt = {
                                **_current_remote_attempt(repo, job.job_id, fallback_artifacts=job.artifacts),
                                **sanitize_remote_details(getattr(exc, "details", None)),
                                "finished_at": now_iso(), "outcome": "http_444", "node": node,
                            }
                            error_message = f"HTTP 444 from {exc.details.get('url', 'unknown')} (node={node}); node blacklisted, will retry with different node"
                            scheduled = _retry_or_fail(
                                repo,
                                job.job_id,
                                error_code="HTTP_444",
                                error_message=error_message,
                                artifacts={"remote_attempt": attempt},
                                delay_seconds=5,
                            )
                            LOG.warning(
                                "HTTP 444 on job %s (node=%s) — %s",
                                job.job_id,
                                node,
                                "will retry with different node" if scheduled else "retry budget exhausted; marked failed",
                            )
                        return DaemonResult(processed=1)
                    # soft block：清除代理缓存后重试，让下一次 acquire 能选到不同的 IP 节点。
                    # 444 的清缓存已下沉到 handle_http_444，这里只处理 soft block。
                    if isinstance(exc, RemoteFetchError) and _is_soft_block_error(exc):
                        bump_retry_hint(job.job_id)
                        node = get_job_node(job.job_id)
                        record_node_outcome(node, "soft_block")
                        _record_account_feedback(repo, job.job_id, "soft_block")
                        cleared = clear_proxy_cache()
                        if cleared:
                            LOG.info(
                                "Cleared %d proxy cache entries after soft-block on job %s",
                                cleared,
                                job.job_id,
                            )
                        retry_delay = _jittered_retry_delay(job.retry_count)
                        attempt = {
                            **_current_remote_attempt(repo, job.job_id, fallback_artifacts=job.artifacts),
                            **sanitize_remote_details(getattr(exc, "details", None)),
                            "finished_at": now_iso(), "outcome": "soft_block", "node": node,
                            "retry_delay_seconds": retry_delay,
                        }
                        error_message = f"Soft block / CF challenge from {exc.details.get('url', 'unknown') if isinstance(getattr(exc, 'details', None), dict) else 'unknown'}; proxy cache cleared, will retry with different node"
                        scheduled = _retry_or_fail(
                            repo,
                            job.job_id,
                            error_code="REMOTE_SOFT_BLOCK",
                            error_message=error_message,
                            artifacts={"remote_attempt": attempt},
                            delay_seconds=retry_delay,
                        )
                        LOG.warning(
                            "Soft block on job %s — %s",
                            job.job_id,
                            "will retry with different proxy" if scheduled else "retry budget exhausted; marked failed",
                        )
                        return DaemonResult(processed=1)
                    # 未知页面类型（很可能是反爬页面 / CF 挑战变体）：清除代理缓存后重试
                    if isinstance(exc, UnexpectedPageError):
                        exc_details = getattr(exc, "details", None) or {}
                        if (
                            exc_details.get("page_type") == "unknown"
                            and classify_error(exc) == "UNEXPECTED_REMOTE_PAGE"
                        ):
                            cleared = clear_proxy_cache()
                            if cleared:
                                LOG.info(
                                    "Cleared %d proxy cache entries after unknown page on job %s",
                                    cleared, job.job_id,
                                )
                            page_title = exc_details.get("page_title", "")
                            LOG.warning(
                                "Unknown page type on job %s — likely anti-bot. title=%s html_len=%s",
                                job.job_id, page_title, exc_details.get("html_length"),
                            )
                            bump_retry_hint(job.job_id)
                            node = get_job_node(job.job_id)
                            record_node_outcome(node, "soft_block")
                            _record_account_feedback(repo, job.job_id, "soft_block")
                            retry_delay = _jittered_retry_delay(job.retry_count)
                            attempt = {
                                **_current_remote_attempt(repo, job.job_id, fallback_artifacts=job.artifacts),
                                **sanitize_remote_details(exc_details),
                                "finished_at": now_iso(), "outcome": "soft_block", "page_type": "unknown", "node": node,
                                "retry_delay_seconds": retry_delay,
                            }
                            error_message = (
                                f"Unknown page type (likely anti-bot) from "
                                f"{exc_details.get('url', 'unknown')}; "
                                f"proxy cache cleared, will retry with different node. "
                                f"title={page_title}"
                            )
                            scheduled = _retry_or_fail(
                                repo,
                                job.job_id,
                                error_code="REMOTE_SOFT_BLOCK",
                                error_message=error_message,
                                artifacts={"remote_attempt": attempt},
                                delay_seconds=retry_delay,
                            )
                            if not scheduled:
                                LOG.warning("Unknown page on job %s exhausted retry budget; marked failed", job.job_id)
                            return DaemonResult(processed=1)
                    if isinstance(exc, RemoteFetchError) and is_http_429_error(exc):
                        bump_retry_hint(job.job_id)
                        node = get_job_node(job.job_id)
                        record_node_outcome(node, "rate_limited")
                        _record_account_feedback(repo, job.job_id, "rate_limited")
                        cleared = clear_proxy_cache()
                        attempt = {
                            **_current_remote_attempt(repo, job.job_id, fallback_artifacts=job.artifacts),
                            **sanitize_remote_details(getattr(exc, "details", None)),
                            "finished_at": now_iso(), "outcome": "rate_limited", "node": node,
                        }
                        attempt["proxy_cache_cleared"] = cleared
                        scheduled = _retry_or_fail(
                            repo,
                            job.job_id,
                            error_code="HTTP_429",
                            error_message=str(exc),
                            artifacts={"remote_attempt": attempt},
                            delay_seconds=10,
                        )
                        LOG.warning(
                            "HTTP 429 rate limit on job %s, %s",
                            job.job_id,
                            "retrying later" if scheduled else "retry budget exhausted; marked failed",
                        )
                        return DaemonResult(processed=1)
                    node = get_job_node(job.job_id)
                    details = sanitize_remote_details(getattr(exc, "details", None))
                    artifacts = {
                        "failure_context": {
                            "exception_type": exc.__class__.__name__,
                            "message": str(exc),
                        }
                    }
                    if details:
                        artifacts["failure_context"]["remote_fetch"] = details
                    if isinstance(exc, RemoteFetchError):
                        outcome = outcome_for_error(classify_error(exc), str(exc))
                        record_node_outcome(node, outcome)
                        _record_account_feedback(repo, job.job_id, outcome)
                        retry_delay = 5
                        rotation_required = False
                        if outcome in {"timeout", "connection_error"}:
                            # A timeout is target-specific evidence, not a
                            # reason to keep the same cached candidate.  The
                            # next attempt explicitly excludes this job's
                            # previous node/account histories as well.
                            bump_retry_hint(job.job_id)
                            cleared = clear_proxy_cache()
                            retry_delay = _jittered_retry_delay(job.retry_count, base_seconds=20)
                            rotation_required = True
                        else:
                            cleared = 0
                        artifacts["remote_attempt"] = {
                            **_current_remote_attempt(repo, job.job_id, fallback_artifacts=job.artifacts),
                            **details,
                            "finished_at": now_iso(), "outcome": outcome, "node": node,
                            "retry_delay_seconds": retry_delay,
                            "proxy_cache_cleared": cleared,
                            "rotation_required": rotation_required,
                        }
                    retry_delay = artifacts.get("remote_attempt", {}).get("retry_delay_seconds", 5) \
                        if isinstance(artifacts.get("remote_attempt"), dict) else 5
                    should_retry = (
                        isinstance(exc, RemoteFetchError)
                        and isinstance(getattr(exc, "details", None), dict)
                        and exc.details.get("retryable")
                    )
                    retried = should_retry and repo.retry_later(
                        job.job_id,
                        error_code=classify_error(exc),
                        error_message=str(exc),
                        artifacts=artifacts,
                        delay_seconds=int(retry_delay),
                    )
                    if retried:
                        LOG.warning("Job %s moved to retrying after transient remote fetch failure", job.job_id)
                    else:
                        repo.fail(job.job_id, classify_error(exc), str(exc), artifacts=artifacts)
                return DaemonResult(processed=1)
        except LeaseNotAcquired:
            return DaemonResult(processed=0)
        finally:
            from yamibo_mcp.yamibo.proxy_pool import clear_job_state
            if job is not None:
                clear_job_state(job.job_id)
            conn.close()

    def _publish_worker_heartbeat(self, conn, settings: Settings) -> None:
        interval = max(float(getattr(settings, "worker_heartbeat_seconds", 15)), 1.0)
        now = time.monotonic()
        if now - self._last_worker_heartbeat < interval:
            return
        SystemStateRepository(conn).set_json(
            f"worker_heartbeat:{self.worker_id}",
            {
                "worker_id": self.worker_id,
                "status": "running" if getattr(settings, "jobs_enabled", True) else "paused",
                "heartbeat_at": utc_now_iso(),
            },
        )
        self._last_worker_heartbeat = now

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
                    try:
                        result = self.run_once()
                    except Exception:  # noqa: BLE001 - keep daemon alive on transient failures
                        LOG.exception("Daemon %s crashed outside job handler loop", self.worker_id)
                        emit(LOG, logging.ERROR, "daemon.worker_crashed",
                             f"Daemon {self.worker_id} crashed outside job handler loop",
                             result="failure", status="crashed")
                        if stop_event.wait(self._worker_poll_seconds()):
                            break
                        continue
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
        LOG.info("Maintenance loop started (forum size cache + staging cleanup)")
        emit(LOG, logging.INFO, "maintenance.cache_refresh_started",
             "Maintenance loop started",
             result="success", status="running",
             tags=["maintenance", "cache"])
        # staging 清理计数：每 12 小时（与 cache 刷新同频）执行一次，
        # 清理超过 48 小时的残留目录，防止异常/崩溃后遗留的临时数据堆积。
        while not stop_event.is_set():
            try:
                refresh_forum_size_cache(self.settings)
            except Exception:  # noqa: BLE001 - background maintenance should not stop the daemon
                LOG.exception("Failed to refresh forum size cache")
                emit(LOG, logging.ERROR, "maintenance.cache_refresh_failed",
                     "Failed to refresh forum size cache",
                     result="failure", status="error",
                     tags=["maintenance", "cache"])
            try:
                paths = StoragePaths(self.settings.data_dir)
                older_than = getattr(self.settings, "cleanup_staging_older_than_hours", 48)
                cleanup_stale_staging(paths, older_than_hours=older_than)
            except Exception:  # noqa: BLE001 - background maintenance should not stop the daemon
                LOG.debug("Staging cleanup skipped", exc_info=True)
            if stop_event.wait(FORUM_SIZE_CACHE_REFRESH_SECONDS):
                break
