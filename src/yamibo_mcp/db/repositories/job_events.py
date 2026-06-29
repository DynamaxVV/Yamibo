from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from yamibo_mcp.domain.models import JobEvent
from yamibo_mcp.time_utils import utc_now_iso

LOG = logging.getLogger(__name__)


def _event_from_row(row: sqlite3.Row) -> JobEvent:
    payload_raw = row["payload_json"]
    if payload_raw in {None, ""}:
        payload = {}
    elif isinstance(payload_raw, dict):
        payload = payload_raw
    else:
        payload = json.loads(payload_raw)
    return JobEvent(
        event_id=row["event_id"],
        job_id=row["job_id"],
        event_type=row["event_type"],
        status=row["status"],
        stage=row["stage"],
        payload=payload,
        created_at=row["created_at"],
    )


class JobEventsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(
        self,
        *,
        job_id: str,
        event_type: str,
        status: str | None = None,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JobEvent:
        now = utc_now_iso()
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        cur = self.conn.execute(
            """
            INSERT INTO job_events (job_id, event_type, status, stage, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING event_id
            """,
            (job_id, event_type, status, stage, payload_json, now),
        )
        row = cur.fetchone()
        self.conn.commit()
        event_id = int(row["event_id"]) if row is not None else 0
        return JobEvent(
            event_id=event_id,
            job_id=job_id,
            event_type=event_type,
            status=status,
            stage=stage,
            payload=payload or {},
            created_at=now,
        )

    def append_many(
        self,
        events: list[dict[str, Any]],
    ) -> None:
        if not events:
            return
        now = utc_now_iso()
        rows = [
            (
                str(event["job_id"]),
                str(event["event_type"]),
                event.get("status"),
                event.get("stage"),
                json.dumps(event.get("payload") or {}, ensure_ascii=False),
                now,
            )
            for event in events
        ]
        self.conn.executemany(
            """
            INSERT INTO job_events (job_id, event_type, status, stage, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def list(
        self,
        *,
        job_id: str | None = None,
        since_event_id: int | None = None,
        limit: int = 100,
    ) -> list[JobEvent]:
        conditions: list[str] = []
        params: list[object] = []
        if job_id is not None:
            conditions.append("job_id = ?")
            params.append(job_id)
        if since_event_id is not None:
            conditions.append("event_id > ?")
            params.append(since_event_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM job_events {where} ORDER BY event_id ASC LIMIT ?",
            params,
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM job_events").fetchone()
        return int(row["c"]) if row is not None else 0
