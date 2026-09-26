from __future__ import annotations

import json
import time
import uuid
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback):
    return fallback if value is None else json.loads(value)


class AssistantEvidenceRepository:
    """Persist frozen discussion scopes and bounded source-read receipts."""

    def __init__(self, conn: Any):
        self.conn = conn

    def insert_scope(self, scope: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO chat_discussion_scopes
            (scope_id, owner_id, session_id, run_id, mode, forum_ids, tids, pids,
             start_at, end_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scope["scope_id"], scope["owner_id"], scope["session_id"], scope["run_id"],
                scope["mode"], _json(scope["forum_ids"]), _json(scope["tids"]),
                _json(scope["pids"]), scope.get("start_at"), scope.get("end_at"),
                scope["created_at"],
            ),
        )

    def get_scope(self, scope_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM chat_discussion_scopes WHERE scope_id = ?", (scope_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            **dict(row.items()),
            "forum_ids": _loads(row["forum_ids"], []),
            "tids": _loads(row["tids"], []),
            "pids": _loads(row["pids"], []),
        }

    def insert_receipt(self, receipt: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO chat_source_receipts
            (receipt_id, scope_id, owner_id, session_id, run_id, tid, pid, floor_no,
             content_hash, paragraph_start, paragraph_end, partial_paragraph, content, truncated, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                receipt["receipt_id"], receipt["scope_id"], receipt["owner_id"],
                receipt["session_id"], receipt["run_id"], receipt["tid"], receipt["pid"],
                receipt["floor_no"], receipt["content_hash"], receipt["paragraph_start"],
                receipt["paragraph_end"], receipt.get("partial_paragraph"), receipt["content"], receipt["truncated"],
                receipt["created_at"],
            ),
        )

    def get_receipts(self, run_id: str, receipt_ids: list[str]) -> list[dict[str, Any]]:
        if not receipt_ids:
            return []
        rows = self.conn.execute(
            f"SELECT * FROM chat_source_receipts WHERE run_id = ? AND receipt_id IN ({', '.join('?' for _ in receipt_ids)})",
            (run_id, *receipt_ids),
        ).fetchall()
        return [dict(row.items()) for row in rows]

    def list_receipts(self, run_id: str, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        count = self.conn.execute(
            "SELECT COUNT(*) AS total FROM chat_source_receipts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        rows = self.conn.execute(
            """SELECT receipt_id, scope_id, owner_id, session_id, run_id, tid, pid,
                      floor_no, content_hash, paragraph_start, paragraph_end,
                      partial_paragraph, content, truncated, created_at
               FROM chat_source_receipts WHERE run_id = ?
               ORDER BY created_at, receipt_id LIMIT ? OFFSET ?""",
            (run_id, limit, offset),
        ).fetchall()
        return [dict(row.items()) for row in rows], int(count["total"])

    def read_floor(
        self, tid: int, pid: int, *, metadata_only: bool = False,
        expected_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        # Validate forum/time/sync metadata before loading source text. Recheck
        # those exact values in the content query to close the intervening race.
        columns = "t.tid, t.forum_id, t.content_kind, t.sync_time, t.last_pid, f.pid, f.floor_no, f.pub_time"
        if not metadata_only:
            columns += ", f.content"
        where = "t.tid = ? AND f.pid = ?"
        args: list[Any] = [tid, pid]
        if expected_metadata is not None:
            for column in ("t.forum_id", "t.content_kind", "t.sync_time", "f.pub_time"):
                value = expected_metadata[column.split(".")[1]]
                if value is None:
                    where += f" AND {column} IS NULL"
                else:
                    where += f" AND {column} = ?"
                    args.append(value)
        row = self.conn.execute(
            f"SELECT {columns} FROM threads t JOIN floors f ON f.tid = t.tid WHERE {where}",
            tuple(args),
        ).fetchone()
        return dict(row.items()) if row is not None else None

    def has_discussion_forum(self, forum_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM forums WHERE forum_id = ? AND content_kind = ? AND enabled = ?",
            (forum_id, "discussion", True if getattr(self.conn, "backend", None) in {"postgres", "postgresql"} else 1),
        ).fetchone()
        return row is not None

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def now() -> float:
        return time.time()
