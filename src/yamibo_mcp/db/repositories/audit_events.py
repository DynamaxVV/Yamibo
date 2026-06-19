from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


class AuditEventsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(
        self,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        self.conn.execute(
            """
            INSERT INTO audit_events (
              event_id, actor, action, target_type, target_id, before_json, after_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                actor,
                action,
                target_type,
                target_id,
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
            ),
        )
        return event_id

    def list_recent(self, *, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
