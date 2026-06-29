from __future__ import annotations

import sqlite3
import struct
from typing import Any


class RagVectorUnavailableError(RuntimeError):
    pass


def serialize_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


class SqliteVectorRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._loaded = False

    def ensure_extension_loaded(self) -> None:
        if self._loaded:
            return
        try:
            import sqlite_vec
        except ImportError as exc:
            raise RagVectorUnavailableError("sqlite-vec Python package is not installed") from exc
        try:
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
        except Exception as exc:  # pragma: no cover - depends on local extension loading
            raise RagVectorUnavailableError(f"failed to load sqlite-vec extension: {exc}") from exc
        finally:
            try:
                self.conn.enable_load_extension(False)
            except Exception:
                pass
        self._loaded = True

    def ensure_schema(self, *, dimensions: int) -> str:
        self.ensure_extension_loaded()
        self.conn.execute("DROP TABLE IF EXISTS rag_chunk_vec")
        self.conn.execute(f"CREATE VIRTUAL TABLE rag_chunk_vec USING vec0(embedding float[{dimensions}])")
        version = self.conn.execute("SELECT vec_version()").fetchone()[0]
        self.conn.commit()
        return str(version)

    def reset_if_dimensions_changed(self, *, dimensions: int) -> str:
        self.ensure_extension_loaded()
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'rag_chunk_vec'"
        ).fetchone()
        expected = f"float[{dimensions}]"
        if row is None or expected not in str(row["sql"] or ""):
            return self.ensure_schema(dimensions=dimensions)
        version = self.conn.execute("SELECT vec_version()").fetchone()[0]
        return str(version)

    def replace_embeddings(self, items: list[tuple[int, list[float]]]) -> None:
        self.ensure_extension_loaded()
        for rowid, embedding in items:
            self.conn.execute(
                "INSERT OR REPLACE INTO rag_chunk_vec(rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_f32(embedding)),
            )
        self.conn.commit()

    def delete_thread_embeddings(self, rowids: list[int]) -> None:
        if not rowids:
            return
        self.ensure_extension_loaded()
        placeholders = ",".join("?" for _ in rowids)
        self.conn.execute(f"DELETE FROM rag_chunk_vec WHERE rowid IN ({placeholders})", rowids)
        self.conn.commit()

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        forum_id: int | None = None,
        content_kind: str | None = None,
        tid: int | None = None,
        series_id: int | None = None,
        floor_start: int | None = None,
        floor_end: int | None = None,
    ) -> list[sqlite3.Row]:
        self.ensure_extension_loaded()
        filters: list[str] = []
        params: list[object] = [serialize_f32(query_embedding)]
        if forum_id is not None:
            filters.append("c.forum_id = ?")
            params.append(forum_id)
        if content_kind is not None:
            filters.append("c.content_kind = ?")
            params.append(content_kind)
        if tid is not None:
            filters.append("c.tid = ?")
            params.append(tid)
        if series_id is not None:
            filters.append("c.series_id = ?")
            params.append(series_id)
        if floor_start is not None:
            filters.append("(c.floor_no IS NULL OR c.floor_no >= ?)")
            params.append(floor_start)
        if floor_end is not None:
            filters.append("(c.floor_no IS NULL OR c.floor_no <= ?)")
            params.append(floor_end)
        where = " AND ".join(filters)
        if where:
            where = " AND " + where
        where = f"{where} AND k = ?"
        params.append(top_k)
        return self.conn.execute(
            f"""
            SELECT c.*, v.distance AS vector_distance
            FROM rag_chunk_vec v
            JOIN rag_chunks c ON c.id = v.rowid
            WHERE v.embedding MATCH ?{where}
            ORDER BY v.distance
            """,
            params,
        ).fetchall()


def get_vector_repository(conn: Any):
    backend = getattr(conn, "backend", None)
    if backend in {"postgres", "postgresql"}:
        from yamibo_mcp.db.repositories.postgres_vectors import PostgresVectorRepository

        return PostgresVectorRepository(conn)
    return SqliteVectorRepository(conn)
