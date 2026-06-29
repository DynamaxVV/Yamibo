from __future__ import annotations

import sqlite3


class ForumsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_forums(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM forums ORDER BY forum_id").fetchall()

    def get_forum(self, forum_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM forums WHERE forum_id = ?", (forum_id,)).fetchone()

    def list_profiles(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT forum_id, name, name_en, content_kind, base_url, enabled FROM forums ORDER BY forum_id"
        ).fetchall()

    def count_forums(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM forums").fetchone()
        return int(row["c"]) if row is not None else 0
