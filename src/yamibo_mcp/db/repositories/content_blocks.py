from __future__ import annotations

import json
import sqlite3

from yamibo_mcp.domain.models import ContentBlock


class ContentBlocksRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_blocks(self, tid: int, blocks: list[ContentBlock]) -> None:
        self.conn.execute("DELETE FROM content_blocks WHERE tid = ?", (tid,))
        if not blocks:
            return
        self.conn.executemany(
            """
            INSERT INTO content_blocks (tid, pid, order_index, block_type, text, asset_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    tid,
                    block.pid,
                    block.order_index,
                    block.block_type,
                    block.text,
                    block.asset_id,
                    json.dumps(block.metadata, ensure_ascii=False) if block.metadata else "{}",
                )
                for block in blocks
            ],
        )

    def list_blocks(self, tid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM content_blocks WHERE tid = ? ORDER BY order_index ASC",
            (tid,),
        ).fetchall()

    def list_blocks_for_pids(self, tid: int, pids: list[int]) -> list[sqlite3.Row]:
        if not pids:
            return []
        placeholders = ",".join("?" for _ in pids)
        return self.conn.execute(
            f"SELECT * FROM content_blocks WHERE tid = ? AND pid IN ({placeholders}) ORDER BY order_index ASC",
            (tid, *pids),
        ).fetchall()

    def delete_blocks(self, tid: int) -> None:
        self.conn.execute("DELETE FROM content_blocks WHERE tid = ?", (tid,))

    def count_blocks(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM content_blocks").fetchone()
        return int(row["c"]) if row is not None else 0
