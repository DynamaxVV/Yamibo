from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from typing import Any, TypeVar
from uuid import uuid4

from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.domain.job_state import new_job_id
from yamibo_mcp.domain.models import Job
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.errors import JobNotFound, LeaseNotAcquired
from yamibo_mcp.structured_logging import emit
from yamibo_mcp.time_utils import utc_after_iso, utc_now_iso

LOG = logging.getLogger(__name__)
T = TypeVar("T")

_LOCK_RETRY_DELAYS_SECONDS = (0.1, 0.2, 0.5, 1.0, 2.0)
_REMOTE_ATTEMPT_HISTORY_KEYS = ("nodes_tried", "account_ids_tried")
_REMOTE_ATTEMPT_HISTORY_LIMIT = 20
_REMOTE_ATTEMPT_HISTORY_FIELD = "history"

_LIVE_JOB_STATUSES = (
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.CANCEL_REQUESTED.value,
    JobStatus.PAUSED.value,
    JobStatus.INTERRUPTED.value,
)


def _remote_attempt_history_entry(attempt: dict[str, Any]) -> dict[str, Any]:
    """Return a compact immutable snapshot suitable for bounded history."""
    excluded = {*_REMOTE_ATTEMPT_HISTORY_KEYS, _REMOTE_ATTEMPT_HISTORY_FIELD}
    return {key: value for key, value in attempt.items() if key not in excluded}


def _merge_artifacts(current: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    """Merge artifacts while preserving remote-attempt history without stale fields."""
    merged = {**current}
    if not update:
        return merged
    for key, value in update.items():
        if key == "remote_attempt" and isinstance(value, dict):
            previous = merged.get(key) if isinstance(merged.get(key), dict) else {}
            incoming_started = value.get("started_at")
            previous_started = previous.get("started_at")
            new_attempt = bool(
                incoming_started
                and previous_started
                and incoming_started != previous_started
            )

            prior_history = previous.get(_REMOTE_ATTEMPT_HISTORY_FIELD)
            history = list(prior_history) if isinstance(prior_history, list) else []
            if new_attempt:
                previous_entry = _remote_attempt_history_entry(previous)
                if previous_entry:
                    history.append(previous_entry)
                    history = history[-_REMOTE_ATTEMPT_HISTORY_LIMIT:]
                remote_attempt = dict(value)
            else:
                remote_attempt = {**previous, **value}

            for history_key in _REMOTE_ATTEMPT_HISTORY_KEYS:
                cumulative: list[Any] = []
                for source in (previous.get(history_key), value.get(history_key)):
                    if not isinstance(source, list):
                        continue
                    for item in source:
                        if item and item not in cumulative:
                            cumulative.append(item)
                if cumulative:
                    remote_attempt[history_key] = cumulative

            if history:
                remote_attempt[_REMOTE_ATTEMPT_HISTORY_FIELD] = history
            merged[key] = remote_attempt
        else:
            merged[key] = value
    return merged


def _job_list_order_clause() -> str:
    # 状态发生变化时 updated_at 会刷新，最近变更的任务排在最前。
    return "updated_at DESC, created_at DESC, job_id DESC"


def _loads(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    return json.loads(value)


def _is_retryable_lock_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "database is locked" in message or "database table is locked" in message:
        return True
    sqlstate = getattr(exc, "sqlstate", None) or getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate in {"55P03", "40001", "40P01"}:
        return True
    pgcode = getattr(exc, "pgcode", None) or getattr(getattr(exc, "orig", None), "pgcode", None)
    return pgcode in {"55P03", "40001", "40P01"}


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        job_type=row["job_type"],
        status=row["status"],
        stage=row["stage"],
        tid=row["tid"],
        payload=_loads(row["payload_json"]),
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        worker_id=row["worker_id"],
        heartbeat_at=row["heartbeat_at"],
        lease_until=row["lease_until"],
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        resumable=bool(row["resumable"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        artifacts=_loads(row["artifacts_json"]),
        paused_at=row["paused_at"] if "paused_at" in row.keys() else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
        priority=int(row["priority"] or 0) if "priority" in row.keys() else 0,
        started_at=row["started_at"] if "started_at" in row.keys() else None,
    )


class JobsRepository:
    def __init__(self, conn: sqlite3.Connection, *, owner_id: str | None = None):
        self.conn = conn
        # ``owner_id`` identifies the logical daemon worker.  ``_lease_tokens``
        # is the actual fencing token for each acquisition; it changes every
        # time a job is claimed, including when the same worker id reclaims it.
        self.owner_id = owner_id
        self._lease_tokens: dict[str, str] = {}

    def _lease_guard(
        self,
        job_id: str,
        *,
        now: str,
        statuses: tuple[str, ...],
        worker_id: str | None = None,
    ) -> tuple[str, list[Any], bool]:
        """Build a conditional-write predicate for the current job owner.

        Unbound repositories are also used by the web/admin and legacy test
        paths.  They retain their historical job-id-only mutation semantics.
        Daemon repositories are bound to an owner and, after acquisition, use
        the per-claim token as a fencing value.  The lease-time check prevents
        a delayed heartbeat or finalizer from resurrecting an expired claim.
        """
        token = self._lease_tokens.get(job_id)
        owner = self.owner_id or worker_id
        if token is not None:
            placeholders = ", ".join("?" for _ in statuses)
            worker_condition = " AND worker_id = ?" if worker_id is not None else ""
            worker_params = [worker_id] if worker_id is not None else []
            return (
                f"job_id = ? AND lease_token = ?{worker_condition} AND status IN ({placeholders}) "
                "AND lease_until IS NOT NULL AND lease_until >= ?",
                [job_id, token, *worker_params, *statuses, now],
                True,
            )
        if owner is not None:
            placeholders = ", ".join("?" for _ in statuses)
            return (
                f"job_id = ? AND worker_id = ? AND status IN ({placeholders}) "
                "AND lease_until IS NOT NULL AND lease_until >= ?",
                [job_id, owner, *statuses, now],
                True,
            )
        return "job_id = ?", [job_id], False

    def assert_lease(self, job_id: str, *, for_update: bool = False) -> None:
        """Verify this repository still owns a running job.

        ``for_update=True`` is used at the start of a domain-data transaction.
        On PostgreSQL it locks the job row for the transaction, so expiry
        recovery cannot transfer ownership between the check and the archive
        writes.  SQLite's enclosing ``BEGIN IMMEDIATE`` provides the same
        serialization for its single-writer connection.
        """
        token = self._lease_tokens.get(job_id)
        owner = self.owner_id
        if token is None and owner is None:
            return
        now = utc_now_iso()
        if token is not None:
            predicate = "job_id = ? AND lease_token = ?"
            params: list[Any] = [job_id, token]
        else:
            predicate = "job_id = ? AND worker_id = ?"
            params = [job_id, owner]
        sql = f"""
            SELECT job_id
            FROM jobs
            WHERE {predicate}
              AND status = ?
              AND lease_until IS NOT NULL
              AND lease_until >= ?
        """
        params.extend([JobStatus.RUNNING.value, now])
        if for_update and getattr(self.conn, "backend", None) in {"postgres", "postgresql"}:
            sql += " FOR UPDATE"
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            raise LeaseNotAcquired(f"Lease lost for job {job_id}")

    def _raise_if_lease_lost(self, job_id: str, *, guarded: bool, rowcount: int) -> None:
        if guarded and rowcount != 1:
            raise LeaseNotAcquired(f"Lease lost for job {job_id}")

    def _with_locked_retry(self, operation: Callable[[], T]) -> T:
        for delay in (*_LOCK_RETRY_DELAYS_SECONDS, None):
            try:
                return operation()
            except Exception as exc:  # noqa: BLE001 - backend-specific lock failures are normalized here
                if not _is_retryable_lock_error(exc) or delay is None:
                    raise
                self.conn.rollback()
                time.sleep(delay)
        raise AssertionError("unreachable")

    def _append_event(
        self,
        job_id: str,
        event_type: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            from yamibo_mcp.db.repositories.job_events import JobEventsRepository
            JobEventsRepository(self.conn).append(
                job_id=job_id,
                event_type=event_type,
                status=status,
                stage=stage,
                payload=payload,
            )
        except Exception:
            LOG.warning("Failed to append job event %s for job %s", event_type, job_id, exc_info=True)

    def _append_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        try:
            from yamibo_mcp.db.repositories.job_events import JobEventsRepository
            JobEventsRepository(self.conn).append_many(events)
        except Exception:
            LOG.warning("Failed to append %s job events", len(events), exc_info=True)

    def create(
        self,
        job_type: str,
        *,
        tid: int | None = None,
        payload: dict[str, Any] | None = None,
        parent_job_id: str | None = None,
        max_retries: int = 3,
        resumable: bool = True,
    ) -> Job:
        normalized_payload = payload or {}
        now = utc_now_iso()
        payload_json = json.dumps(normalized_payload, ensure_ascii=False)
        superseded_job_ids: list[str] = []
        if tid is not None:
            queued_matches = self._list_queued_matches(job_type=job_type, tid=tid)
            exact_matches = [row for row in queued_matches if _loads(row["payload_json"]) == normalized_payload]
            if exact_matches:
                keeper = exact_matches[-1]
                superseded_job_ids = [row["job_id"] for row in exact_matches[:-1]]
                self._delete_jobs_without_commit(superseded_job_ids)
                self.conn.commit()
                LOG.info(
                    "Reused queued job %s for job_type=%s tid=%s payload_deduped=%s",
                    keeper["job_id"],
                    job_type,
                    tid,
                    len(exact_matches),
                )
                return self.get(keeper["job_id"])

        job_id = new_job_id(job_type)
        # create 是所有控制面的统一入队入口，Server/Web 只负责把任务写成 queued。
        self.conn.execute(
            """
            INSERT INTO jobs (
              job_id, parent_job_id, job_type, tid, payload_json, status,
              max_retries, resumable, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                parent_job_id,
                job_type,
                tid,
                payload_json,
                JobStatus.QUEUED.value,
                max_retries,
                resumable,
                now,
                now,
            ),
        )
        if tid is not None:
            queued_matches = self._list_queued_matches(job_type=job_type, tid=tid)
            exact_matches = [row for row in queued_matches if _loads(row["payload_json"]) == normalized_payload]
            superseded_job_ids = [row["job_id"] for row in exact_matches if row["job_id"] != job_id]
            if superseded_job_ids:
                self._delete_jobs_without_commit(superseded_job_ids)
        self.conn.commit()
        self._append_event(job_id, "job.created", status=JobStatus.QUEUED.value)
        emit(LOG, logging.INFO, "job.created", f"Job {job_id} created",
             result="success", status=JobStatus.QUEUED.value,
             job_id=job_id, job_type=job_type, tid=tid)
        return self.get(job_id)

    def rerun(self, job_id: str) -> Job:
        source = self.get(job_id)
        if source.status not in (JobStatus.PARTIAL.value, JobStatus.FAILED.value, JobStatus.INTERRUPTED.value):
            raise ValueError("Only partial, failed, or interrupted jobs can be retried")

        next_job_id = new_job_id(source.job_type)
        now = utc_now_iso()
        try:
            self.conn.execute(
                """
                INSERT INTO jobs (
                  job_id, parent_job_id, job_type, tid, payload_json, status,
                  max_retries, resumable, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_job_id,
                    None,
                    source.job_type,
                    source.tid,
                    json.dumps(source.payload or {}, ensure_ascii=False),
                    JobStatus.QUEUED.value,
                    source.max_retries,
                    source.resumable,
                    now,
                    now,
                ),
            )
            self._delete_jobs_without_commit([source.job_id])
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self._append_event(next_job_id, "job.created", status=JobStatus.QUEUED.value)
        return self.get(next_job_id)

    def resync_failed_floor_job(self, job_id: str) -> tuple[Job, int]:
        """Queue a full sync and remove this thread's failed jobs atomically."""
        try:
            source = self.get(job_id)
            # Serialize recovery from different failed jobs of the same archive.
            self.conn.execute(
                "UPDATE threads SET tid = tid WHERE tid = ?", (source.tid,)
            )
            self.conn.execute(
                "UPDATE jobs SET status = status WHERE job_id = ?", (job_id,)
            )
            source = self.get(job_id)
            if not (
                source.status == "failed"
                and source.job_type == "image_backfill"
                and source.stage == "load_local"
                and source.tid
                and (source.error_message or "").startswith(
                    "local floor sequence is invalid; full resync required"
                )
            ):
                raise ValueError("Only failed local floor sequence backfill jobs can be resynced")
            payload = {"tid": source.tid}
            live = self.find_live_job_for_thread(job_type="sync_thread", tid=source.tid)
            if live and live.payload != payload:
                raise ValueError("A different sync job already exists for this thread; wait for it to finish")
            next_job_id = live.job_id if live else new_job_id("sync_thread")
            now = utc_now_iso()
            if live is None:
                self.conn.execute(
                    """INSERT INTO jobs (
                        job_id, job_type, tid, payload_json, status,
                        max_retries, resumable, created_at, updated_at
                    ) VALUES (?, 'sync_thread', ?, ?, 'queued', 3, ?, ?, ?)""",
                    (next_job_id, source.tid, json.dumps(payload), True, now, now),
                )
            # Lock the records selected for cleanup against concurrent state changes.
            self.conn.execute(
                "UPDATE jobs SET status = status WHERE tid = ? AND status = 'failed'",
                (source.tid,),
            )
            failed_ids = [row["job_id"] for row in self.conn.execute(
                "SELECT job_id FROM jobs WHERE tid = ? AND status = 'failed'", (source.tid,)
            ).fetchall()]
            self._delete_jobs_without_commit(failed_ids)
            self.conn.execute(
                """INSERT INTO job_events
                    (job_id, event_type, status, payload_json, created_at)
                    VALUES (?, 'job.floor_recovery_requested', ?, ?, ?)""",
                (next_job_id, live.status if live else "queued", json.dumps({
                    "source_job_id": job_id, "deleted_failed_job_ids": failed_ids,
                    "tid": source.tid,
                }), now),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(next_job_id), len(failed_ids)

    def get(self, job_id: str) -> Job:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return _job_from_row(row)

    def list(self, *, limit: int | None = 100, offset: int = 0, status: str | None = None) -> list[Job]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        else:
            clauses.append("status != ?")
            params.append("superseded")
        sql = "SELECT * FROM jobs"
        if clauses:
            sql += f" WHERE {' AND '.join(clauses)}"
        sql += f" ORDER BY {_job_list_order_clause()}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, max(offset, 0)])
        rows = self.conn.execute(sql, params).fetchall()
        return [_job_from_row(row) for row in rows]

    def count_filtered(self, *, status: str | None = None) -> int:
        if status:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = ?", (status,)).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status != ?", ("superseded",)).fetchone()
        return int(row["n"] or 0)

    def count_by_job_type_status(self, *, job_type: str, statuses: tuple[str, ...]) -> int:
        if not statuses:
            return 0
        placeholders = ",".join("?" for _ in statuses)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs WHERE job_type = ? AND status IN ({placeholders})",
            (job_type, *statuses),
        ).fetchone()
        return int(row["n"] or 0)

    def find_live_job_for_thread(
        self, *, job_type: str, tid: int, payload: dict[str, Any] | None = None,
    ) -> Job | None:
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM jobs
            WHERE job_type = ?
              AND tid = ?
              AND status IN ({",".join("?" for _ in _LIVE_JOB_STATUSES)})
            ORDER BY created_at DESC, job_id DESC
            {"LIMIT 1" if payload is None else ""}
            """,
            (job_type, tid, *_LIVE_JOB_STATUSES),
        ).fetchall()
        for row in rows:
            job = _job_from_row(row)
            existing_payload = job.payload
            if job_type == "sync_thread":
                existing_payload = {"mode": "full", **existing_payload}
            if payload is None or existing_payload == payload:
                return job
        return None

    def get_latest_job(
        self,
        *,
        job_type: str,
        tid: int,
        statuses: tuple[str, ...] | list[str] | None = None,
    ) -> Job | None:
        conditions = ["job_type = ?", "tid = ?"]
        params: list[object] = [job_type, tid]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            conditions.append(f"status IN ({placeholders})")
            params.extend(statuses)
        row = self.conn.execute(
            f"""
            SELECT *
            FROM jobs
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, job_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return None
        return _job_from_row(row)

    def get_latest_child_job(self, parent_job_id: str) -> Job | None:
        row = self.conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE parent_job_id = ?
            ORDER BY created_at DESC, job_id DESC
            LIMIT 1
            """,
            (parent_job_id,),
        ).fetchone()
        if row is None:
            return None
        return _job_from_row(row)

    def get_latest_child_jobs(self, parent_job_ids: list[str]) -> dict[str, Job]:
        unique_parent_ids = sorted({job_id for job_id in parent_job_ids if job_id})
        if not unique_parent_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_parent_ids)
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM jobs
            WHERE parent_job_id IN ({placeholders})
            ORDER BY parent_job_id ASC, created_at DESC, job_id DESC
            """,
            unique_parent_ids,
        ).fetchall()
        latest: dict[str, Job] = {}
        for row in rows:
            parent_job_id = str(row["parent_job_id"])
            if parent_job_id not in latest:
                latest[parent_job_id] = _job_from_row(row)
        return latest

    def acquire_next(self, worker_id: str, lease_seconds: int) -> Job | None:
        # Worker 只抢还没被占用、或者租约已经过期的任务。
        row = self.conn.execute(
            """
            SELECT job_id FROM jobs
            WHERE status IN (?, ?, ?)
              AND (lease_until IS NULL OR lease_until < ?)
            ORDER BY
              priority ASC,
              CASE WHEN job_type = 'daily_sign_in' THEN -1 ELSE 0 END ASC,
              CASE
                WHEN status = ? THEN 0
                WHEN status = ? THEN 1
                WHEN status = ? THEN 2
                ELSE 3
              END ASC,
              created_at ASC,
              job_id ASC
            LIMIT 1
            """,
            (
                JobStatus.QUEUED.value,
                JobStatus.RETRYING.value,
                JobStatus.INTERRUPTED.value,
                utc_now_iso(),
                JobStatus.QUEUED.value,
                JobStatus.RETRYING.value,
                JobStatus.INTERRUPTED.value,
            ),
        ).fetchone()
        if row is None:
            return None
        LOG.info("acquire_next selected job_id=%s worker_id=%s", row["job_id"], worker_id)
        return self.acquire(row["job_id"], worker_id, lease_seconds)

    def _list_queued_matches(self, *, job_type: str, tid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE job_type = ?
              AND tid = ?
              AND status = ?
            ORDER BY created_at ASC, job_id ASC
            """,
            (job_type, tid, JobStatus.QUEUED.value),
        ).fetchall()

    def _delete_jobs_without_commit(self, job_ids: list[str]) -> None:
        if not job_ids:
            return
        placeholders = ",".join("?" for _ in job_ids)
        self.conn.execute(f"DELETE FROM job_events WHERE job_id IN ({placeholders})", job_ids)
        self.conn.execute(f"DELETE FROM jobs WHERE job_id IN ({placeholders})", job_ids)

    def acquire(self, job_id: str, worker_id: str, lease_seconds: int) -> Job:
        def _acquire() -> sqlite3.Cursor:
            now = utc_now_iso()
            lease_until = utc_after_iso(lease_seconds)
            lease_token = uuid4().hex
            # 通过条件 UPDATE 做租约抢占，保证多 worker 下只有一个执行者能拿到任务。
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = ?, lease_token = ?, heartbeat_at = ?, lease_until = ?,
                    stage = COALESCE(stage, 'acquired'), started_at = ?, updated_at = ?
                WHERE job_id = ?
                  AND status IN (?, ?, ?)
                  AND (lease_until IS NULL OR lease_until < ?)
                """,
                (
                    JobStatus.RUNNING.value,
                    worker_id,
                    lease_token,
                    now,
                    lease_until,
                    now,
                    now,
                    job_id,
                    JobStatus.QUEUED.value,
                    JobStatus.RETRYING.value,
                    JobStatus.INTERRUPTED.value,
                    now,
                ),
            )
            self.conn.commit()
            if cur.rowcount:
                self._lease_tokens[job_id] = lease_token
                SystemStateRepository(self.conn).set_json(
                    f"worker_heartbeat:{worker_id}",
                    {
                        "worker_id": worker_id,
                        "status": "running",
                        "heartbeat_at": now,
                    },
                )
            else:
                self._lease_tokens.pop(job_id, None)
            return cur

        cur = self._with_locked_retry(_acquire)
        if cur.rowcount != 1:
            raise LeaseNotAcquired(job_id)
        self._append_event(
            job_id,
            "job.started",
            status=JobStatus.RUNNING.value,
            stage="acquired",
            payload={"worker_id": worker_id},
        )
        emit(LOG, logging.INFO, "job.started", f"Job {job_id} started by {worker_id}",
             result="success", status=JobStatus.RUNNING.value, stage="acquired")
        return self.get(job_id)

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        def _heartbeat() -> None:
            now = utc_now_iso()
            lease_until = utc_after_iso(lease_seconds)
            guard, guard_params, _guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.RUNNING.value, JobStatus.PAUSED.value),
                worker_id=worker_id,
            )
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET heartbeat_at = ?, lease_until = ?, updated_at = ?
                WHERE {guard}
                """,
                [now, lease_until, now, *guard_params],
            )
            self.conn.commit()
            if cur.rowcount:
                SystemStateRepository(self.conn).set_json(
                    f"worker_heartbeat:{worker_id}",
                    {
                        "worker_id": worker_id,
                        "status": "running",
                        "heartbeat_at": now,
                    },
                )
            elif self.owner_id is not None:
                raise LeaseNotAcquired(f"Lease lost for job {job_id}")

        self._with_locked_retry(_heartbeat)

    def update_stage(
        self,
        job_id: str,
        stage: str,
        *,
        progress_current: int | None = None,
        progress_total: int | None = None,
    ) -> None:
        current = "progress_current" if progress_current is None else "?"
        total = "progress_total" if progress_total is None else "?"
        params: list[Any] = [stage]
        if progress_current is not None:
            params.append(progress_current)
        if progress_total is not None:
            params.append(progress_total)
        now = utc_now_iso()
        guard, guard_params, guarded = self._lease_guard(
            job_id,
            now=now,
            statuses=(JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value),
        )
        params.extend([now, *guard_params])
        def _update_stage() -> None:
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET stage = ?, progress_current = {current}, progress_total = {total},
                    updated_at = ?
                WHERE {guard}
                """,
                params,
            )
            self.conn.commit()
            self._raise_if_lease_lost(job_id, guarded=guarded, rowcount=cur.rowcount)

        self._with_locked_retry(_update_stage)
        self._append_event(
            job_id,
            "job.progressed",
            stage=stage,
            payload={
                "progress_current": progress_current,
                "progress_total": progress_total,
            },
        )

    def record_remote_attempt(self, job_id: str, attempt: dict[str, Any]) -> None:
        """Persist one sanitized remote-attempt snapshot in existing job JSON/event storage."""
        def _record() -> dict[str, Any]:
            current = self.get(job_id)
            artifacts = current.artifacts if isinstance(current.artifacts, dict) else {}
            previous_attempt = artifacts.get("remote_attempt") if isinstance(artifacts.get("remote_attempt"), dict) else {}
            incoming_started = attempt.get("started_at")
            previous_started = previous_attempt.get("started_at")
            new_attempt = bool(
                incoming_started
                and previous_started
                and incoming_started != previous_started
            )
            next_attempt = dict(attempt) if new_attempt else {**previous_attempt, **dict(attempt)}
            for history_key, current_key in (("nodes_tried", "node"), ("account_ids_tried", "account_id")):
                previous_values = previous_attempt.get(history_key, [])
                values = list(previous_values) if isinstance(previous_values, list) else []
                current_value = attempt.get(current_key)
                if current_value and current_value not in values:
                    values.append(current_value)
                if values:
                    next_attempt[history_key] = values
            next_artifacts = _merge_artifacts(artifacts, {"remote_attempt": next_attempt})
            now = utc_now_iso()
            guard, guard_params, guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value),
            )
            cur = self.conn.execute(
                f"UPDATE jobs SET artifacts_json = ?, updated_at = ? WHERE {guard}",
                [json.dumps(next_artifacts, ensure_ascii=False), now, *guard_params],
            )
            self.conn.commit()
            self._raise_if_lease_lost(job_id, guarded=guarded, rowcount=cur.rowcount)
            return next_artifacts["remote_attempt"]

        remote_attempt = self._with_locked_retry(_record)
        self._append_event(job_id, "job.remote_attempt", payload={"remote_attempt": remote_attempt})

    def succeed(self, job_id: str, artifacts: dict[str, Any] | None = None) -> None:
        def _succeed() -> tuple[bool, dict[str, Any]]:
            now = utc_now_iso()
            current = self.get(job_id)
            next_artifacts = current.artifacts if isinstance(current.artifacts, dict) else {}
            if isinstance(next_artifacts.get("remote_attempt"), dict):
                next_artifacts = {
                    **next_artifacts,
                    "remote_attempt": {**next_artifacts["remote_attempt"], "outcome": "success", "finished_at": now},
                }
            next_artifacts = _merge_artifacts(next_artifacts, artifacts)
            guard, guard_params, guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value),
            )
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, stage = 'finalize', progress_current = COALESCE(progress_total, progress_current),
                    artifacts_json = ?, updated_at = ?, finished_at = ?,
                    error_code = NULL, error_message = NULL, worker_id = NULL,
                    heartbeat_at = NULL, lease_until = NULL, lease_token = NULL
                WHERE {guard}
                """,
                (
                    JobStatus.SUCCEEDED.value,
                    json.dumps(next_artifacts, ensure_ascii=False),
                    now,
                    now,
                    *guard_params,
                ),
            )
            self.conn.commit()
            if cur.rowcount != 1:
                if guarded:
                    raise LeaseNotAcquired(f"Lease lost for job {job_id}")
                return False, next_artifacts
            self._lease_tokens.pop(job_id, None)
            return True, next_artifacts

        applied, next_artifacts = self._with_locked_retry(_succeed)
        if not applied:
            LOG.warning("Ignoring succeed for job %s after lease ownership was lost", job_id)
            return
        self._append_event(
            job_id,
            "job.succeeded",
            status=JobStatus.SUCCEEDED.value,
            payload={"artifacts": next_artifacts},
        )
        emit(LOG, logging.INFO, "job.succeeded", f"Job {job_id} succeeded",
             result="success", status=JobStatus.SUCCEEDED.value, stage="finalize")

    def exclude(
        self,
        job_id: str,
        *,
        reason: str,
        artifacts: dict[str, Any] | None = None,
    ) -> None:
        """Finish a job without archiving it, while keeping an explicit audit trail."""
        def _exclude() -> tuple[bool, dict[str, Any]]:
            now = utc_now_iso()
            current = self.get(job_id)
            next_artifacts = current.artifacts if isinstance(current.artifacts, dict) else {}
            if isinstance(next_artifacts.get("remote_attempt"), dict):
                next_artifacts = {
                    **next_artifacts,
                    "remote_attempt": {
                        **next_artifacts["remote_attempt"],
                        "outcome": "excluded",
                        "finished_at": now,
                    },
                }
            next_artifacts = _merge_artifacts(
                next_artifacts,
                {
                    "excluded": True,
                    "archive_status": "excluded",
                    "exclusion_reason": reason,
                    **(artifacts or {}),
                },
            )
            guard, guard_params, guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value),
            )
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, stage = 'finalize', progress_current = COALESCE(progress_total, progress_current),
                    artifacts_json = ?, updated_at = ?, finished_at = ?,
                    error_code = NULL, error_message = NULL, worker_id = NULL,
                    heartbeat_at = NULL, lease_until = NULL, lease_token = NULL
                WHERE {guard}
                """,
                (
                    JobStatus.SUCCEEDED.value,
                    json.dumps(next_artifacts, ensure_ascii=False),
                    now,
                    now,
                    *guard_params,
                ),
            )
            self.conn.commit()
            if cur.rowcount != 1:
                if guarded:
                    raise LeaseNotAcquired(f"Lease lost for job {job_id}")
                return False, next_artifacts
            self._lease_tokens.pop(job_id, None)
            return True, next_artifacts

        applied, next_artifacts = self._with_locked_retry(_exclude)
        if not applied:
            LOG.warning("Ignoring exclude for job %s after lease ownership was lost", job_id)
            return
        self._append_event(
            job_id,
            "job.excluded",
            status=JobStatus.SUCCEEDED.value,
            payload={"reason": reason, "artifacts": next_artifacts},
        )
        emit(
            LOG,
            logging.INFO,
            "job.excluded",
            f"Job {job_id} excluded from archive: {reason}",
            result="success",
            status=JobStatus.SUCCEEDED.value,
            stage="finalize",
            exclusion_reason=reason,
        )

    def fail(
        self,
        job_id: str,
        error_code: str,
        error_message: str,
        artifacts: dict[str, Any] | None = None,
    ) -> None:
        def _fail() -> tuple[bool, dict[str, Any]]:
            now = utc_now_iso()
            current = self.get(job_id)
            next_artifacts = current.artifacts if isinstance(current.artifacts, dict) else {}
            next_artifacts = _merge_artifacts(next_artifacts, artifacts)
            guard, guard_params, guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value),
            )
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, error_code = ?, error_message = ?,
                    artifacts_json = ?, updated_at = ?, finished_at = ?, worker_id = NULL,
                    heartbeat_at = NULL, lease_until = NULL, lease_token = NULL
                WHERE {guard}
                """,
                (
                    JobStatus.FAILED.value,
                    error_code,
                    error_message,
                    json.dumps(next_artifacts, ensure_ascii=False),
                    now,
                    now,
                    *guard_params,
                ),
            )
            self.conn.commit()
            if cur.rowcount != 1:
                if guarded:
                    raise LeaseNotAcquired(f"Lease lost for job {job_id}")
                return False, next_artifacts
            self._lease_tokens.pop(job_id, None)
            return True, next_artifacts

        applied, next_artifacts = self._with_locked_retry(_fail)
        if not applied:
            LOG.warning("Ignoring fail for job %s after lease ownership was lost", job_id)
            return
        self._append_event(
            job_id,
            "job.failed",
            status=JobStatus.FAILED.value,
            payload={
                "error_code": error_code,
                "error_message": error_message,
                "artifacts": next_artifacts,
            },
        )
        emit(LOG, logging.ERROR, "job.failed", f"Job {job_id} failed: {error_code}",
             result="failure", status=JobStatus.FAILED.value,
             error_code=error_code, error_message=error_message)

    def retry_later(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
        artifacts: dict[str, Any] | None = None,
        delay_seconds: int | None = None,
        max_delay_seconds: int = 60,
    ) -> bool:
        def _retry_later() -> tuple[sqlite3.Cursor | None, dict[str, Any], bool]:
            now = utc_now_iso()
            current = self.get(job_id)
            if current.retry_count >= current.max_retries:
                return None, {}, False
            retry_delay = min(
                int(delay_seconds) if delay_seconds is not None else 5 * (2 ** current.retry_count),
                max(1, int(max_delay_seconds)),
            )
            # PONETAIL: retrying jobs reuse lease_until as a not-before timestamp;
            # split it into next_retry_at only if scheduling semantics expand.
            next_retry_at = utc_after_iso(retry_delay)
            next_artifacts = current.artifacts if isinstance(current.artifacts, dict) else {}
            next_artifacts = _merge_artifacts(next_artifacts, artifacts)
            guard, guard_params, _guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.RUNNING.value,),
            )
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, retry_count = retry_count + 1, error_code = ?, error_message = ?,
                    artifacts_json = ?, worker_id = NULL, heartbeat_at = NULL, lease_until = ?,
                    lease_token = NULL, updated_at = ?
                WHERE {guard}
                """,
                (
                    JobStatus.RETRYING.value,
                    error_code,
                    error_message,
                    json.dumps(next_artifacts, ensure_ascii=False),
                    next_retry_at,
                    now,
                    *guard_params,
                ),
            )
            self.conn.commit()
            return cur, next_artifacts, _guarded

        cur, next_artifacts, guarded = self._with_locked_retry(_retry_later)
        if cur is None:
            return False
        if cur.rowcount != 1:
            if guarded:
                raise LeaseNotAcquired(f"Lease lost for job {job_id}")
            return False
        refreshed = self.get(job_id)
        self._append_event(
            job_id,
            "job.retrying",
            status=JobStatus.RETRYING.value,
            payload={
                "error_code": error_code,
                "error_message": error_message,
                "retry_count": refreshed.retry_count,
                "max_retries": refreshed.max_retries,
                "next_retry_at": refreshed.lease_until,
                "artifacts": next_artifacts,
            },
        )
        emit(LOG, logging.WARNING, "job.retrying", f"Job {job_id} retrying ({refreshed.retry_count}/{refreshed.max_retries}): {error_code}",
             result="retry", status=JobStatus.RETRYING.value,
             error_code=error_code, error_message=error_message,
             attempt=refreshed.retry_count)
        return True

    def partial(self, job_id: str, artifacts: dict[str, Any] | None = None) -> None:
        def _partial() -> tuple[bool, dict[str, Any]]:
            now = utc_now_iso()
            current = self.get(job_id)
            next_artifacts = current.artifacts if isinstance(current.artifacts, dict) else {}
            if isinstance(next_artifacts.get("remote_attempt"), dict):
                next_artifacts = {
                    **next_artifacts,
                    "remote_attempt": {**next_artifacts["remote_attempt"], "outcome": "success", "finished_at": now},
                }
            next_artifacts = _merge_artifacts(next_artifacts, artifacts)
            guard, guard_params, guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value),
            )
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, stage = 'finalize', progress_current = COALESCE(progress_total, progress_current),
                    artifacts_json = ?, updated_at = ?, finished_at = ?,
                    error_code = NULL, error_message = NULL, worker_id = NULL,
                    heartbeat_at = NULL, lease_until = NULL, lease_token = NULL
                WHERE {guard}
                """,
                (
                    JobStatus.PARTIAL.value,
                    json.dumps(next_artifacts, ensure_ascii=False),
                    now,
                    now,
                    *guard_params,
                ),
            )
            self.conn.commit()
            if cur.rowcount != 1:
                if guarded:
                    raise LeaseNotAcquired(f"Lease lost for job {job_id}")
                return False, next_artifacts
            self._lease_tokens.pop(job_id, None)
            return True, next_artifacts

        applied, next_artifacts = self._with_locked_retry(_partial)
        if not applied:
            LOG.warning("Ignoring partial for job %s after lease ownership was lost", job_id)
            return
        self._append_event(
            job_id,
            "job.partial",
            status=JobStatus.PARTIAL.value,
            payload={"artifacts": next_artifacts},
        )

    def request_cancel(self, job_id: str) -> bool:
        def _request_cancel() -> sqlite3.Cursor:
            now = utc_now_iso()
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, cancel_requested_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (JobStatus.CANCEL_REQUESTED.value, now, now, job_id, JobStatus.RUNNING.value),
            )
            self.conn.commit()
            return cur

        cur = self._with_locked_retry(_request_cancel)
        return cur.rowcount > 0

    def pause(self, job_id: str) -> bool:
        def _pause() -> sqlite3.Cursor:
            now = utc_now_iso()
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, paused_at = COALESCE(paused_at, ?), updated_at = ?
                WHERE job_id = ? AND status IN (?, ?, ?, ?)
                """,
                (
                    JobStatus.PAUSED.value,
                    now,
                    now,
                    job_id,
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                    JobStatus.RETRYING.value,
                    JobStatus.INTERRUPTED.value,
                ),
            )
            self.conn.commit()
            return cur

        cur = self._with_locked_retry(_pause)
        if cur.rowcount > 0:
            self._append_event(job_id, "job.paused", status=JobStatus.PAUSED.value)
            emit(LOG, logging.INFO, "job.paused", f"Job {job_id} paused",
                 result="success", status=JobStatus.PAUSED.value)
        return cur.rowcount > 0

    def finalize_pause(self, job_id: str) -> bool:
        def _finalize_pause() -> sqlite3.Cursor:
            now = utc_now_iso()
            guard, guard_params, guarded = self._lease_guard(
                job_id,
                now=now,
                statuses=(JobStatus.PAUSED.value,),
            )
            cur = self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, worker_id = NULL, heartbeat_at = NULL, lease_until = NULL,
                    lease_token = NULL, updated_at = ?
                WHERE {guard}
                """,
                (JobStatus.PAUSED.value, now, *guard_params),
            )
            self.conn.commit()
            if cur.rowcount == 1:
                self._lease_tokens.pop(job_id, None)
            elif guarded:
                raise LeaseNotAcquired(f"Lease lost for job {job_id}")
            return cur

        cur = self._with_locked_retry(_finalize_pause)
        return cur.rowcount > 0

    def resume(self, job_id: str) -> bool:
        def _resume() -> sqlite3.Cursor:
            now = utc_now_iso()
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, paused_at = NULL, worker_id = NULL, heartbeat_at = NULL,
                    lease_until = NULL, lease_token = NULL, updated_at = ?
                WHERE job_id = ?
                  AND status = ?
                  AND (worker_id IS NULL OR lease_until IS NULL OR lease_until < ?)
                """,
                (JobStatus.QUEUED.value, now, job_id, JobStatus.PAUSED.value, now),
            )
            self.conn.commit()
            return cur

        cur = self._with_locked_retry(_resume)
        if cur.rowcount > 0:
            self._append_event(job_id, "job.resumed", status=JobStatus.QUEUED.value)
            emit(LOG, logging.INFO, "job.resumed", f"Job {job_id} resumed",
                 result="success", status=JobStatus.QUEUED.value)
        return cur.rowcount > 0

    def release_expired_paused_jobs(self) -> int:
        now = utc_now_iso()
        expired_job_ids = [
            row["job_id"]
            for row in self.conn.execute(
                """
                SELECT job_id FROM jobs
                WHERE status = ?
                  AND worker_id IS NOT NULL
                  AND lease_until IS NOT NULL
                  AND lease_until < ?
                """,
                (JobStatus.PAUSED.value, now),
            ).fetchall()
        ]
        if not expired_job_ids:
            return 0
        def _release() -> None:
            self.conn.execute(
                """
                UPDATE jobs
                SET worker_id = NULL, heartbeat_at = NULL, lease_until = NULL, lease_token = NULL, updated_at = ?
                WHERE status = ?
                  AND worker_id IS NOT NULL
                  AND lease_until IS NOT NULL
                  AND lease_until < ?
                """,
                (now, JobStatus.PAUSED.value, now),
            )
            self.conn.commit()

        self._with_locked_retry(_release)
        for job_id in expired_job_ids:
            LOG.info("release_expired_paused_jobs job_id=%s", job_id)
        return len(expired_job_ids)

    def is_cancelled(self, job_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return row is not None and row["status"] == JobStatus.CANCEL_REQUESTED.value

    def is_paused(self, job_id: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return row is not None and row["status"] == JobStatus.PAUSED.value

    def mark_expired_running_interrupted(self) -> int:
        """Recover expired running jobs without allowing stale-job loops.

        A handler that is blocked in I/O can outlive its lease.  Recovery must
        consume the same retry budget as the normal exception path; otherwise
        a job at ``retry_count == max_retries`` is repeatedly interrupted and
        immediately acquired again forever.
        """
        now = utc_now_iso()

        def _recover_expired() -> list[dict[str, Any]]:
            rows = self.conn.execute(
                """
                SELECT job_id, stage, retry_count, max_retries, error_code,
                       error_message
                FROM jobs
                WHERE status = ? AND lease_until IS NOT NULL AND lease_until < ?
                """,
                (JobStatus.RUNNING.value, now),
            ).fetchall()
            recovered: list[dict[str, Any]] = []
            for row in rows:
                retry_count = int(row["retry_count"] or 0)
                max_retries = int(row["max_retries"] or 0)
                retry_budget_exhausted = retry_count >= max_retries
                next_retry_count = retry_count if retry_budget_exhausted else retry_count + 1
                next_status = (
                    JobStatus.FAILED.value
                    if retry_budget_exhausted
                    else JobStatus.INTERRUPTED.value
                )
                error_message = (
                    "job lease expired while handler was running; retry budget exhausted"
                    if retry_budget_exhausted
                    else "job lease expired while handler was running; scheduled for recovery"
                )
                previous_error_code = row["error_code"]
                previous_error_message = row["error_message"]
                cur = self.conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, retry_count = ?, error_code = ?, error_message = ?,
                        worker_id = NULL, heartbeat_at = NULL, lease_until = NULL,
                        lease_token = NULL, updated_at = ?,
                        finished_at = ?
                    WHERE job_id = ? AND status = ?
                      AND lease_until IS NOT NULL AND lease_until < ?
                    """,
                    (
                        next_status,
                        next_retry_count,
                        "LEASE_LOST",
                        error_message,
                        now,
                        now if next_status == JobStatus.FAILED.value else None,
                        row["job_id"],
                        JobStatus.RUNNING.value,
                        now,
                    ),
                )
                if cur.rowcount == 1:
                    recovered.append(
                        {
                            "job_id": row["job_id"],
                            "stage": row["stage"],
                            "status": next_status,
                            "retry_count": next_retry_count,
                            "max_retries": max_retries,
                            "previous_error_code": previous_error_code,
                            "previous_error_message": previous_error_message,
                            "error_message": error_message,
                        }
                    )
            self.conn.commit()
            return recovered

        recovered = self._with_locked_retry(_recover_expired)
        for item in recovered:
            job_id = str(item["job_id"])
            status = str(item["status"])
            event_type = "job.failed" if status == JobStatus.FAILED.value else "job.interrupted"
            LOG.info(
                "mark_expired_running_interrupted job_id=%s status=%s retry_count=%s/%s",
                job_id,
                status,
                item["retry_count"],
                item["max_retries"],
            )
            self._append_event(
                job_id,
                event_type,
                status=status,
                stage=item["stage"],
                payload={
                    "error_code": "LEASE_LOST",
                    "error_message": item["error_message"],
                    "retry_count": item["retry_count"],
                    "max_retries": item["max_retries"],
                    "previous_error_code": item["previous_error_code"],
                    "previous_error_message": item["previous_error_message"],
                },
            )
        return len(recovered)

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
        return int(row["c"]) if row is not None else 0

    def count_by_status(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS cnt FROM jobs WHERE status != ? GROUP BY status", ("superseded",)).fetchall()
        counts = {"all": 0}
        for row in rows:
            counts[str(row["status"])] = int(row["cnt"])
            counts["all"] += int(row["cnt"])
        return counts

    def list_ids_by_status(self, status: str) -> list[str]:
        rows = self.conn.execute("SELECT job_id FROM jobs WHERE status = ? ORDER BY created_at DESC, job_id DESC", (status,)).fetchall()
        return [str(row["job_id"]) for row in rows]

    def job_control_summary(self) -> dict[str, int]:
        counts = self.count_by_status()
        return {
            "queued": counts.get(JobStatus.QUEUED.value, 0),
            "running": counts.get(JobStatus.RUNNING.value, 0),
            "retrying": counts.get(JobStatus.RETRYING.value, 0),
            "interrupted": counts.get(JobStatus.INTERRUPTED.value, 0),
            "paused": counts.get(JobStatus.PAUSED.value, 0),
        }

    def pause_active_jobs(self) -> list[str]:
        statuses = (
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
            JobStatus.RETRYING.value,
            JobStatus.INTERRUPTED.value,
        )
        status_placeholders = ",".join("?" for _ in statuses)
        now = utc_now_iso()

        def _pause_active() -> list[str]:
            rows = self.conn.execute(
                f"""
                SELECT job_id FROM jobs
                WHERE status IN ({status_placeholders})
                ORDER BY created_at DESC, job_id DESC
                """,
                statuses,
            ).fetchall()
            job_ids = [str(row["job_id"]) for row in rows]
            if not job_ids:
                return []
            job_placeholders = ",".join("?" for _ in job_ids)
            self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, paused_at = COALESCE(paused_at, ?), updated_at = ?
                WHERE job_id IN ({job_placeholders})
                  AND status IN ({status_placeholders})
                """,
                (JobStatus.PAUSED.value, now, now, *job_ids, *statuses),
            )
            self.conn.commit()
            return job_ids

        job_ids = self._with_locked_retry(_pause_active)
        self._append_events([
            {"job_id": job_id, "event_type": "job.paused", "status": JobStatus.PAUSED.value}
            for job_id in job_ids
        ])
        return job_ids

    def resume_paused_jobs(self) -> list[str]:
        now = utc_now_iso()

        def _resume_paused() -> list[str]:
            rows = self.conn.execute(
                """
                SELECT job_id FROM jobs
                WHERE status = ?
                  AND (worker_id IS NULL OR lease_until IS NULL OR lease_until < ?)
                ORDER BY created_at DESC, job_id DESC
                """,
                (JobStatus.PAUSED.value, now),
            ).fetchall()
            job_ids = [str(row["job_id"]) for row in rows]
            if not job_ids:
                return []
            job_placeholders = ",".join("?" for _ in job_ids)
            self.conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, paused_at = NULL, worker_id = NULL, heartbeat_at = NULL, lease_until = NULL, updated_at = ?
                WHERE job_id IN ({job_placeholders})
                  AND status = ?
                  AND (worker_id IS NULL OR lease_until IS NULL OR lease_until < ?)
                """,
                (JobStatus.QUEUED.value, now, *job_ids, JobStatus.PAUSED.value, now),
            )
            self.conn.commit()
            return job_ids

        job_ids = self._with_locked_retry(_resume_paused)
        self._append_events([
            {"job_id": job_id, "event_type": "job.resumed", "status": JobStatus.QUEUED.value}
            for job_id in job_ids
        ])
        return job_ids

    def list_recent(self, *, limit: int = 10):
        return self.list(limit=limit)

    def list_live_sync_thread_statuses(self) -> dict[int, str]:
        live_statuses = (
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
            JobStatus.RETRYING.value,
            JobStatus.CANCEL_REQUESTED.value,
            JobStatus.INTERRUPTED.value,
            JobStatus.PAUSED.value,
        )
        rows = self.conn.execute(
            """
            SELECT tid, status
            FROM jobs
            WHERE job_type = 'sync_thread'
              AND tid IS NOT NULL
              AND status IN (?, ?, ?, ?, ?, ?)
            ORDER BY updated_at DESC, created_at DESC
            """,
            live_statuses,
        ).fetchall()
        statuses: dict[int, str] = {}
        for row in rows:
            tid = row["tid"]
            if tid is None or tid in statuses:
                continue
            statuses[int(tid)] = row["status"]
        return statuses

    def list_worker_heartbeats(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT worker_id, MAX(heartbeat_at) AS latest_heartbeat_at,
                   SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_jobs,
                   COUNT(*) AS seen_jobs
            FROM jobs
            WHERE worker_id IS NOT NULL
            GROUP BY worker_id
            HAVING SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) > 0
            ORDER BY COALESCE(MAX(heartbeat_at), MAX(updated_at)) DESC
            LIMIT 20
            """
        ).fetchall()

    def list_running_job_ids(self, job_ids: list[str]) -> list[str]:
        if not job_ids:
            return []
        placeholders = ",".join("?" for _ in job_ids)
        rows = self.conn.execute(
            f"SELECT job_id FROM jobs WHERE job_id IN ({placeholders}) AND status = 'running'",
            job_ids,
        ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def list_recent_errors(self, *, limit: int = 10):
        return self.conn.execute(
            """
            SELECT job_id, error_code, error_message, finished_at
            FROM jobs
            WHERE error_code IS NOT NULL
            ORDER BY finished_at DESC, job_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def delete_job(self, job_id: str) -> None:
        self.conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
        self.conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        self.conn.commit()

    def delete_jobs(self, job_ids: list[str]) -> int:
        if not job_ids:
            return 0
        placeholders = ",".join("?" for _ in job_ids)
        self.conn.execute(f"DELETE FROM job_events WHERE job_id IN ({placeholders})", job_ids)
        self.conn.execute(f"DELETE FROM jobs WHERE job_id IN ({placeholders})", job_ids)
        self.conn.commit()
        return len(job_ids)
