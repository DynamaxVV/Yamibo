from __future__ import annotations

import sqlite3

from yamibo_mcp.domain.models import AssetSnapshot


class AssetsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_assets(self, tid: int, assets: list[AssetSnapshot]) -> None:
        self.conn.execute("DELETE FROM assets WHERE tid = ?", (tid,))
        if not assets:
            return
        self.conn.executemany(
            """
            INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, required, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    asset.asset_id,
                    tid,
                    asset.pid,
                    asset.asset_type,
                    asset.remote_url,
                    asset.local_path,
                    asset.exportable,
                    asset.required,
                    asset.status,
                )
                for asset in assets
            ],
        )

    def list_assets(self, tid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM assets WHERE tid = ? ORDER BY asset_id ASC",
            (tid,),
        ).fetchall()

    def delete_assets(self, tid: int) -> None:
        self.conn.execute("DELETE FROM assets WHERE tid = ?", (tid,))

    def get_asset(self, asset_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()

    def count_assets(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM assets").fetchone()
        return int(row["c"]) if row is not None else 0
