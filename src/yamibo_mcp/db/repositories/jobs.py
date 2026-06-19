from __future__ import annotations

import json
import sqlite3
from typing import Any

from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.domain.job_state import new_job_id
from yamibo_mcp.domain.models import Job
from yamibo_mcp.errors import JobNotFound, LeaseNotAcquired
from yamibo_mcp.time_utils import utc_after_iso, utc_now_iso


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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


class JobsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

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
        job_id = new_job_id(job_type)
        now = utc_now_iso()
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
                json.dumps(payload or {}, ensure_ascii=False),
                JobStatus.QUEUED.value,
                max_retries,
                1 if resumable else 0,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return _job_from_row(row)

    def list(self, *, limit: int = 100, status: str | None = None) -> list[Job]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def acquire_next(self, worker_id: str, lease_seconds: int) -> Job | None:
        # Worker 只抢还没被占用、或者租约已经过期的任务。
        row = self.conn.execute(
            """
            SELECT job_id FROM jobs
            WHERE status IN (?, ?, ?)
              AND (lease_until IS NULL OR lease_until < ?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (
                JobStatus.QUEUED.value,
                JobStatus.RETRYING.value,
                JobStatus.INTERRUPTED.value,
                utc_now_iso(),
            ),
        ).fetchone()
        if row is None:
            return None
        return self.acquire(row["job_id"], worker_id, lease_seconds)

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
        return self.get(job_id)

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        now = utc_now_iso()
        lease_until = utc_after_iso(lease_seconds)
        self.conn.execute(
            """
            UPDATE jobs
            SET heartbeat_at = ?, lease_until = ?, updated_at = ?
            WHERE job_id = ? AND worker_id = ? AND status = ?
            """,
            (now, lease_until, now, job_id, worker_id, JobStatus.RUNNING.value),
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

    def mark_expired_running_interrupted(self) -> int:
        now = utc_now_iso()
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
        return cur.rowcount
