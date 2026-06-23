from __future__ import annotations

import sqlite3
from typing import Any

from yamibo_mcp.rag.chunker import RagChunk
from yamibo_mcp.time_utils import utc_now_iso


class RagChunksRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def replace_thread_chunks(
        self,
        *,
        tid: int,
        chunks: list[RagChunk],
        embedding_model: str,
        embedding_dimensions: int,
    ) -> list[sqlite3.Row]:
        self.conn.execute("DELETE FROM rag_chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM rag_chunks WHERE tid = ?)", (tid,))
        self.conn.execute("DELETE FROM rag_chunks WHERE tid = ?", (tid,))
        now = utc_now_iso()
        for chunk in chunks:
            self.conn.execute(
                """
                INSERT INTO rag_chunks (
                  chunk_id, tid, pid, floor_no, chunk_type, forum_id, content_kind, series_id, series_key,
                  chapter_index, publisher, pub_time, title, metadata_text, text, text_hash, source_uri,
                  embedding_model, embedding_dimensions, embedding_status, indexed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    chunk.chunk_id,
                    chunk.tid,
                    chunk.pid,
                    chunk.floor_no,
                    chunk.chunk_type,
                    chunk.forum_id,
                    chunk.content_kind,
                    chunk.series_id,
                    chunk.series_key,
                    chunk.chapter_index,
                    chunk.publisher,
                    chunk.pub_time,
                    chunk.title,
                    chunk.metadata_text,
                    chunk.text,
                    chunk.text_hash,
                    chunk.source_uri,
                    embedding_model,
                    embedding_dimensions,
                    now,
                    now,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO rag_chunks_fts (chunk_id, title, metadata_text, body)
                VALUES (?, ?, ?, ?)
                """,
                (chunk.chunk_id, chunk.title or "", chunk.metadata_text, chunk.text),
            )
        self.conn.commit()
        return self.list_chunks_for_thread(tid)

    def list_chunks_for_thread(self, tid: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM rag_chunks WHERE tid = ? ORDER BY floor_no IS NULL DESC, floor_no ASC, id ASC",
            (tid,),
        ).fetchall()

    def set_embedding_status(
        self,
        chunk_ids: list[str],
        *,
        status: str,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        if not chunk_ids:
            return
        placeholders = ",".join("?" for _ in chunk_ids)
        params: list[Any] = [status, utc_now_iso()]
        set_sql = "embedding_status = ?, updated_at = ?"
        if embedding_model is not None:
            set_sql += ", embedding_model = ?"
            params.append(embedding_model)
        if embedding_dimensions is not None:
            set_sql += ", embedding_dimensions = ?"
            params.append(embedding_dimensions)
        params.extend(chunk_ids)
        self.conn.execute(
            f"UPDATE rag_chunks SET {set_sql} WHERE chunk_id IN ({placeholders})",
            params,
        )
        self.conn.commit()

    def write_index_meta(self, mapping: dict[str, str]) -> None:
        for key, value in mapping.items():
            self.conn.execute(
                """
                INSERT INTO rag_index_meta(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
        self.conn.commit()

    def read_index_meta(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM rag_index_meta").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def get_chunk_by_id(self, chunk_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM rag_chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[sqlite3.Row]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.conn.execute(
            f"SELECT * FROM rag_chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        by_id = {str(row["chunk_id"]): row for row in rows}
        return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]


    def keyword_search(
        self,
        *,
        query: str,
        top_k: int,
        forum_id: int | None = None,
        content_kind: str | None = None,
        tid: int | None = None,
        series_id: int | None = None,
        floor_start: int | None = None,
        floor_end: int | None = None,
    ) -> list[sqlite3.Row]:
        filters: list[str] = []
        params: list[Any] = [query]
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
        params.append(top_k)
        rows = self.conn.execute(
            f"""
            SELECT c.*, bm25(rag_chunks_fts) AS keyword_score
            FROM rag_chunks_fts
            JOIN rag_chunks c ON c.chunk_id = rag_chunks_fts.chunk_id
            WHERE rag_chunks_fts MATCH ?{where}
            ORDER BY bm25(rag_chunks_fts)
            LIMIT ?
            """,
            params,
        ).fetchall()
        if rows:
            return rows

        like_terms = [term.strip() for term in query.split() if term.strip()]
        if not like_terms:
            return []
        filters_sql = ""
        fallback_params: list[Any] = []
        if forum_id is not None:
            filters_sql += " AND forum_id = ?"
            fallback_params.append(forum_id)
        if content_kind is not None:
            filters_sql += " AND content_kind = ?"
            fallback_params.append(content_kind)
        if tid is not None:
            filters_sql += " AND tid = ?"
            fallback_params.append(tid)
        if series_id is not None:
            filters_sql += " AND series_id = ?"
            fallback_params.append(series_id)
        if floor_start is not None:
            filters_sql += " AND (floor_no IS NULL OR floor_no >= ?)"
            fallback_params.append(floor_start)
        if floor_end is not None:
            filters_sql += " AND (floor_no IS NULL OR floor_no <= ?)"
            fallback_params.append(floor_end)
        term_sql = " AND ".join("(title LIKE ? OR metadata_text LIKE ? OR text LIKE ?)" for _ in like_terms)
        for term in like_terms:
            like = f"%{term}%"
            fallback_params.extend([like, like, like])
        fallback_params.append(top_k)
        return self.conn.execute(
            f"""
            SELECT *, 0.0 AS keyword_score
            FROM rag_chunks
            WHERE {term_sql}{filters_sql}
            ORDER BY floor_no IS NULL DESC, floor_no ASC, id ASC
            LIMIT ?
            """,
            fallback_params,
        ).fetchall()
