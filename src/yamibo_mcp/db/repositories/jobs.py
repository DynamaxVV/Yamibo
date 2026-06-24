from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.domain.job_state import new_job_id
from yamibo_mcp.domain.models import Job
from yamibo_mcp.errors import JobNotFound, LeaseNotAcquired
from yamibo_mcp.time_utils import utc_after_iso, utc_now_iso

LOG = logging.getLogger(__name__)

_LIVE_JOB_STATUSES = (
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.CANCEL_REQUESTED.value,
    JobStatus.PAUSED.value,
    JobStatus.INTERRUPTED.value,
)


def _job_list_order_clause() -> str:
    # 任务列表按“进行中 -> 排队中 -> 其他完成态”分组，同组内按创建时间升序，保证启动较早的任务靠前。
    return """
    CASE
        WHEN status IN ('running', 'retrying', 'cancel_requested', 'paused') THEN 0
        WHEN status = 'queued' THEN 1
        ELSE 2
    END ASC,
    created_at ASC,
    job_id ASC
    """


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    return json.loads(value)


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
    )


class JobsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

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
                self._mark_superseded_rows(superseded_job_ids, superseded_by_job_id=keeper["job_id"], now=now)
                self.conn.commit()
                for job_id in superseded_job_ids:
                    self._append_event(
                        job_id,
                        "job.superseded",
                        status=JobStatus.SUPERSEDED.value,
                        payload={"superseded_by_job_id": keeper["job_id"]},
                    )
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
                1 if resumable else 0,
                now,
                now,
            ),
        )
        if tid is not None:
            queued_matches = self._list_queued_matches(job_type=job_type, tid=tid)
            exact_matches = [row for row in queued_matches if _loads(row["payload_json"]) == normalized_payload]
            superseded_job_ids = [row["job_id"] for row in exact_matches if row["job_id"] != job_id]
            if superseded_job_ids:
                self._mark_superseded_rows(superseded_job_ids, superseded_by_job_id=job_id, now=now)
        self.conn.commit()
        self._append_event(job_id, "job.created", status=JobStatus.QUEUED.value)
        for superseded_job_id in superseded_job_ids:
            self._append_event(
                superseded_job_id,
                "job.superseded",
                status=JobStatus.SUPERSEDED.value,
                payload={"superseded_by_job_id": job_id},
            )
        return self.get(job_id)

    def rerun(self, job_id: str) -> Job:
        source = self.get(job_id)
        if source.status not in (JobStatus.PARTIAL.value, JobStatus.FAILED.value):
            raise ValueError("Only partial or failed jobs can be retried")

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
                    source.job_id,
                    source.job_type,
                    source.tid,
                    json.dumps(source.payload or {}, ensure_ascii=False),
                    JobStatus.QUEUED.value,
                    source.max_retries,
                    1 if source.resumable else 0,
                    now,
                    now,
                ),
            )
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?, finished_at = ?, lease_until = NULL
                WHERE job_id = ? AND status IN (?, ?)
                """,
                (
                    JobStatus.SUPERSEDED.value,
                    now,
                    now,
                    source.job_id,
                    JobStatus.PARTIAL.value,
                    JobStatus.FAILED.value,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("Failed to supersede source job")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        self._append_event(next_job_id, "job.created", status=JobStatus.QUEUED.value)
        self._append_event(
            source.job_id,
            "job.superseded",
            status=JobStatus.SUPERSEDED.value,
            payload={"superseded_by_job_id": next_job_id},
        )
        return self.get(next_job_id)

    def get(self, job_id: str) -> Job:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return _job_from_row(row)

    def list(self, *, limit: int = 100, status: str | None = None) -> list[Job]:
        if status:
            rows = self.conn.execute(
                f"SELECT * FROM jobs WHERE status = ? ORDER BY {_job_list_order_clause()} LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM jobs ORDER BY {_job_list_order_clause()} LIMIT ?",
                (limit,),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def find_live_job_for_thread(self, *, job_type: str, tid: int) -> Job | None:
        row = self.conn.execute(
            f"""
            SELECT *
            FROM jobs
            WHERE job_type = ?
              AND tid = ?
              AND status IN ({",".join("?" for _ in _LIVE_JOB_STATUSES)})
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (job_type, tid, *_LIVE_JOB_STATUSES),
        ).fetchone()
        if row is None:
            return None
        return _job_from_row(row)

    def acquire_next(self, worker_id: str, lease_seconds: int) -> Job | None:
        # Worker 只抢还没被占用、或者租约已经过期的任务。
        row = self.conn.execute(
            """
            SELECT job_id FROM jobs
            WHERE status IN (?, ?, ?)
              AND (lease_until IS NULL OR lease_until < ?)
            ORDER BY
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

    def _mark_superseded_rows(self, job_ids: list[str], *, superseded_by_job_id: str, now: str) -> None:
        if not job_ids:
            return
        self.conn.executemany(
            """
            UPDATE jobs
            SET status = ?, updated_at = ?, finished_at = ?, lease_until = NULL
            WHERE job_id = ? AND status = ?
            """,
            [
                (
                    JobStatus.SUPERSEDED.value,
                    now,
                    now,
                    job_id,
                    JobStatus.QUEUED.value,
                )
                for job_id in job_ids
            ],
        )
        LOG.info(
            "Superseded duplicate queued job(s) %s in favor of %s",
            job_ids,
            superseded_by_job_id,
        )

    def acquire(self, job_id: str, worker_id: str, lease_seconds: int) -> Job:
        now = utc_now_iso()
        lease_until = utc_after_iso(lease_seconds)
        # 通过条件 UPDATE 做租约抢占，保证多 worker 下只有一个执行者能拿到任务。
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, worker_id = ?, heartbeat_at = ?, lease_until = ?,
                stage = COALESCE(stage, 'acquired'), updated_at = ?
            WHERE job_id = ?
              AND status IN (?, ?, ?)
              AND (lease_until IS NULL OR lease_until < ?)
            """,
            (
                JobStatus.RUNNING.value,
                worker_id,
                now,
                lease_until,
                now,
                job_id,
                JobStatus.QUEUED.value,
                JobStatus.RETRYING.value,
                JobStatus.INTERRUPTED.value,
                now,
            ),
        )
        self.conn.commit()
        if cur.rowcount != 1:
            raise LeaseNotAcquired(job_id)
        self._append_event(
            job_id,
            "job.started",
            status=JobStatus.RUNNING.value,
            stage="acquired",
            payload={"worker_id": worker_id},
        )
        return self.get(job_id)

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        now = utc_now_iso()
        lease_until = utc_after_iso(lease_seconds)
        self.conn.execute(
            """
            UPDATE jobs
            SET heartbeat_at = ?, lease_until = ?, updated_at = ?
            WHERE job_id = ? AND worker_id = ? AND status IN (?, ?)
            """,
            (now, lease_until, now, job_id, worker_id, JobStatus.RUNNING.value, JobStatus.PAUSED.value),
        )
        self.conn.commit()

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
        params.extend([utc_now_iso(), job_id])
        self.conn.execute(
            f"""
            UPDATE jobs
            SET stage = ?, progress_current = {current}, progress_total = {total},
                updated_at = ?
            WHERE job_id = ?
            """,
            params,
        )
        self.conn.commit()
        self._append_event(
            job_id,
            "job.progressed",
            stage=stage,
            payload={
                "progress_current": progress_current,
                "progress_total": progress_total,
            },
        )

    def succeed(self, job_id: str, artifacts: dict[str, Any] | None = None) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, stage = 'finalize', progress_current = COALESCE(progress_total, progress_current),
                artifacts_json = ?, updated_at = ?, finished_at = ?,
                lease_until = NULL
            WHERE job_id = ?
            """,
            (
                JobStatus.SUCCEEDED.value,
                json.dumps(artifacts or {}, ensure_ascii=False),
                now,
                now,
                job_id,
            ),
        )
        self.conn.commit()
        self._append_event(
            job_id,
            "job.succeeded",
            status=JobStatus.SUCCEEDED.value,
            payload={"artifacts": artifacts or {}},
        )

    def fail(self, job_id: str, error_code: str, error_message: str) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, error_code = ?, error_message = ?,
                updated_at = ?, finished_at = ?, lease_until = NULL
            WHERE job_id = ?
            """,
            (JobStatus.FAILED.value, error_code, error_message, now, now, job_id),
        )
        self.conn.commit()
        self._append_event(
            job_id,
            "job.failed",
            status=JobStatus.FAILED.value,
            payload={"error_code": error_code, "error_message": error_message},
        )

    def partial(self, job_id: str, artifacts: dict[str, Any] | None = None) -> None:
        now = utc_now_iso()
        self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, stage = 'finalize', progress_current = COALESCE(progress_total, progress_current),
                artifacts_json = ?, updated_at = ?, finished_at = ?, lease_until = NULL
            WHERE job_id = ?
            """,
            (
                JobStatus.PARTIAL.value,
                json.dumps(artifacts or {}, ensure_ascii=False),
                now,
                now,
                job_id,
            ),
        )
        self.conn.commit()
        self._append_event(
            job_id,
            "job.partial",
            status=JobStatus.PARTIAL.value,
            payload={"artifacts": artifacts or {}},
        )

    def request_cancel(self, job_id: str) -> bool:
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
        return cur.rowcount > 0

    def pause(self, job_id: str) -> bool:
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
        if cur.rowcount > 0:
            self._append_event(job_id, "job.paused", status=JobStatus.PAUSED.value)
        return cur.rowcount > 0

    def finalize_pause(self, job_id: str) -> bool:
        now = utc_now_iso()
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, worker_id = NULL, heartbeat_at = NULL, lease_until = NULL, updated_at = ?
            WHERE job_id = ? AND status = ?
            """,
            (JobStatus.PAUSED.value, now, job_id, JobStatus.PAUSED.value),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def resume(self, job_id: str) -> bool:
        now = utc_now_iso()
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, paused_at = NULL, worker_id = NULL, heartbeat_at = NULL, lease_until = NULL, updated_at = ?
            WHERE job_id = ?
              AND status = ?
              AND (worker_id IS NULL OR lease_until IS NULL OR lease_until < ?)
            """,
            (JobStatus.QUEUED.value, now, job_id, JobStatus.PAUSED.value, now),
        )
        self.conn.commit()
        if cur.rowcount > 0:
            self._append_event(job_id, "job.resumed", status=JobStatus.QUEUED.value)
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
        self.conn.execute(
            """
            UPDATE jobs
            SET worker_id = NULL, heartbeat_at = NULL, lease_until = NULL, updated_at = ?
            WHERE status = ?
              AND worker_id IS NOT NULL
              AND lease_until IS NOT NULL
              AND lease_until < ?
            """,
            (now, JobStatus.PAUSED.value, now),
        )
        self.conn.commit()
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
        now = utc_now_iso()
        expired_job_ids = [
            row["job_id"]
            for row in self.conn.execute(
                """
                SELECT job_id FROM jobs
                WHERE status = ? AND lease_until IS NOT NULL AND lease_until < ?
                """,
                (JobStatus.RUNNING.value, now),
            ).fetchall()
        ]
        # Worker 重启时先把超时的 running 标成 interrupted，后续再重新抢占执行。
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, updated_at = ?
            WHERE status = ? AND lease_until IS NOT NULL AND lease_until < ?
            """,
            (
                JobStatus.INTERRUPTED.value,
                now,
                JobStatus.RUNNING.value,
                now,
            ),
        )
        self.conn.commit()
        for job_id in expired_job_ids:
            LOG.info("mark_expired_running_interrupted job_id=%s", job_id)
            self._append_event(
                job_id,
                "job.interrupted",
                status=JobStatus.INTERRUPTED.value,
            )
        return cur.rowcount
