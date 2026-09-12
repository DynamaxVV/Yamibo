from __future__ import annotations

import json
import sqlite3
from typing import Any

from yamibo_mcp.time_utils import utc_now_iso


class SystemStateRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_json(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT value_json FROM system_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        value = row["value_json"]
        if value is None or value == "":
            return None
        if isinstance(value, dict):
            return value
        return json.loads(value)

    def list_json(self, prefix: str = "") -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT key, value_json, updated_at FROM system_state WHERE key LIKE ? ORDER BY key",
            (f"{prefix}%",),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = row["value_json"]
            if value is None or value == "":
                continue
            if not isinstance(value, dict):
                value = json.loads(value)
            result.append({"key": row["key"], "value": value, "updated_at": row["updated_at"]})
        return result

    def set_json(self, key: str, value: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO system_state (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), utc_now_iso()),
        )
        self.conn.commit()

    def delete(self, key: str) -> None:
        self.conn.execute("DELETE FROM system_state WHERE key = ?", (key,))
        self.conn.commit()
