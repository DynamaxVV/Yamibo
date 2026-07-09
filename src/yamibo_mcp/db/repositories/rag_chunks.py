from __future__ import annotations

import json
import sqlite3
from typing import Any

from yamibo_mcp.rag.chunker import RagChunk
from yamibo_mcp.time_utils import utc_now_iso


class RagChunksRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _uses_sqlite_fts(self) -> bool:
        backend = getattr(self.conn, "backend", None)
        return backend not in {"postgres", "postgresql"}

    def _like_operator(self) -> str:
        backend = getattr(self.conn, "backend", None)
        return "ILIKE" if backend in {"postgres", "postgresql"} else "LIKE"

    def _rag_thread_filter_clauses(
        self,
        *,
        q: str = "",
        forum_id: int | None = None,
        index_state: str = "unindexed",
        rag_status: str = "all",
    ) -> tuple[list[str], list[object]]:
        filters: list[str] = []
        args: list[object] = []
        if q:
            like = f"%{q}%"
            op = self._like_operator()
            filters.append(f"(COALESCE(t.display_title, t.raw_title) {op} ? OR COALESCE(t.publisher, '') {op} ?)")
            args.extend([like, like])
        if forum_id is not None:
            filters.append("t.forum_id = ?")
            args.append(forum_id)
        if index_state == "indexed":
            filters.append("COALESCE(rag_stats.indexed_chunk_count, 0) > 0 AND COALESCE(rag_jobs.live_job_count, 0) = 0")
        elif index_state == "indexing":
            filters.append("COALESCE(rag_jobs.live_job_count, 0) > 0")
        elif index_state == "unindexed":
            filters.append("(COALESCE(rag_stats.indexed_chunk_count, 0) = 0 OR COALESCE(rag_jobs.live_job_count, 0) > 0)")
        if rag_status == "pending":
            filters.append("(COALESCE(rag_stats.chunk_count, 0) = 0 OR COALESCE(rag_stats.pending_chunk_count, 0) > 0)")
        elif rag_status == "failed":
            filters.append("COALESCE(rag_stats.failed_chunk_count, 0) > 0")
        return filters, args

    def replace_thread_chunks(
        self,
        *,
        tid: int,
        chunks: list[RagChunk],
        embedding_model: str,
        embedding_dimensions: int,
    ) -> list[sqlite3.Row]:
        if self._uses_sqlite_fts():
            self.conn.execute(
                "DELETE FROM rag_chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM rag_chunks WHERE tid = ?)",
                (tid,),
            )
        self.conn.execute("DELETE FROM rag_chunks WHERE tid = ?", (tid,))
        now = utc_now_iso()
        for chunk in chunks:
            self.conn.execute(
                """
                INSERT INTO rag_chunks (
                  chunk_id, tid, pid, floor_no, chunk_type, forum_id, content_kind, series_id, series_key,
                  chapter_index, publisher, pub_time, title, metadata_text, text, text_hash, source_uri,
                  source_tid, source_pid, source_floor_no, cleaner_version, chunker_version,
                  materializer_version, source_hash, generated_at, quality_flags,
                  embedding_model, embedding_dimensions, embedding_status, indexed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
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
                    chunk.source_tid,
                    chunk.source_pid,
                    chunk.source_floor_no,
                    chunk.cleaner_version,
                    chunk.chunker_version,
                    chunk.materializer_version,
                    chunk.source_hash,
                    chunk.generated_at,
                    json.dumps(chunk.quality_flags, ensure_ascii=False),
                    embedding_model,
                    embedding_dimensions,
                    now,
                    now,
                ),
            )
            if self._uses_sqlite_fts():
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

    def count_thread_rag_stats(self, tid: int) -> dict[str, object]:
        row = self.conn.execute(
            """
            SELECT
              COUNT(*) AS chunk_count,
              SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
              SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
              SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count,
              MAX(indexed_at) AS last_indexed_at
            FROM rag_chunks
            WHERE tid = ?
            """,
            (tid,),
        ).fetchone()
        return {
            "chunk_count": int(row["chunk_count"] or 0) if row is not None else 0,
            "indexed_chunk_count": int(row["indexed_chunk_count"] or 0) if row is not None else 0,
            "pending_chunk_count": int(row["pending_chunk_count"] or 0) if row is not None else 0,
            "failed_chunk_count": int(row["failed_chunk_count"] or 0) if row is not None else 0,
            "last_indexed_at": None if row is None else row["last_indexed_at"],
        }

    def count_rag_overview(self) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT
              COUNT(*) AS total_threads,
              SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN 1 ELSE 0 END) AS indexed_threads,
              SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) = 0 THEN 1 ELSE 0 END) AS unindexed_threads,
              SUM(CASE WHEN COALESCE(rag_stats.total_chunk_count, 0) > 0 THEN rag_stats.total_chunk_count ELSE 0 END) AS total_chunks,
              SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN rag_stats.indexed_chunk_count ELSE 0 END) AS indexed_chunks,
              SUM(CASE WHEN COALESCE(rag_stats.pending_chunk_count, 0) > 0 THEN rag_stats.pending_chunk_count ELSE 0 END) AS pending_chunks,
              SUM(CASE WHEN COALESCE(rag_stats.failed_chunk_count, 0) > 0 THEN rag_stats.failed_chunk_count ELSE 0 END) AS failed_chunks
            FROM threads t
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS total_chunk_count,
                SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
                SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
                SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count
              FROM rag_chunks
              GROUP BY tid
            ) rag_stats ON rag_stats.tid = t.tid
            """
        ).fetchone()
        return {
            "total_threads": int(row["total_threads"] or 0) if row is not None else 0,
            "indexed_threads": int(row["indexed_threads"] or 0) if row is not None else 0,
            "unindexed_threads": int(row["unindexed_threads"] or 0) if row is not None else 0,
            "total_chunks": int(row["total_chunks"] or 0) if row is not None else 0,
            "indexed_chunks": int(row["indexed_chunks"] or 0) if row is not None else 0,
            "pending_chunks": int(row["pending_chunks"] or 0) if row is not None else 0,
            "failed_chunks": int(row["failed_chunks"] or 0) if row is not None else 0,
        }

    def count_floor_coverage_diagnostics(self) -> dict[str, object]:
        eligible_row = self.conn.execute(
            """
            SELECT COUNT(*) AS eligible_floor_count
            FROM floors
            WHERE NULLIF(TRIM(COALESCE(content, '')), '') IS NOT NULL
            """
        ).fetchone()
        indexed_row = self.conn.execute(
            """
            SELECT COUNT(*) AS indexed_floor_count
            FROM (
              SELECT DISTINCT tid, floor_no
              FROM rag_chunks
              WHERE floor_no IS NOT NULL
                AND embedding_status = 'indexed'
            ) indexed_floors
            """
        ).fetchone()
        eligible_floor_count = int(eligible_row["eligible_floor_count"] or 0) if eligible_row is not None else 0
        indexed_floor_count = int(indexed_row["indexed_floor_count"] or 0) if indexed_row is not None else 0
        skipped_non_empty_floor_count = max(eligible_floor_count - indexed_floor_count, 0)
        skipped_reasons: list[dict[str, object]] = []
        if skipped_non_empty_floor_count:
            skipped_reasons.append(
                {
                    "reason": "non_empty_floor_without_indexed_chunk",
                    "count": skipped_non_empty_floor_count,
                }
            )
        return {
            "rag_full_floor_policy": "all_non_empty_text_floors",
            "eligible_floor_count": eligible_floor_count,
            "indexed_floor_count": indexed_floor_count,
            "skipped_non_empty_floor_count": skipped_non_empty_floor_count,
            "skipped_reasons": skipped_reasons,
        }

    def list_rag_forum_breakdown(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
              f.forum_id,
              f.name,
              f.name_en,
              f.content_kind,
              COUNT(DISTINCT t.tid) AS thread_count,
              SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN 1 ELSE 0 END) AS indexed_thread_count,
              SUM(CASE WHEN COALESCE(rag_stats.total_chunk_count, 0) > 0 THEN rag_stats.total_chunk_count ELSE 0 END) AS chunk_count
            FROM forums f
            LEFT JOIN threads t ON t.forum_id = f.forum_id
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS total_chunk_count,
                SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count
              FROM rag_chunks
              GROUP BY tid
            ) rag_stats ON rag_stats.tid = t.tid
            GROUP BY f.forum_id, f.name, f.name_en, f.content_kind
            ORDER BY f.forum_id
            """
        ).fetchall()

    def list_rag_threads(
        self,
        *,
        q: str = "",
        forum_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
        index_state: str = "unindexed",
        rag_status: str = "all",
    ) -> dict[str, object]:
        filters, args = self._rag_thread_filter_clauses(
            q=q,
            forum_id=forum_id,
            index_state=index_state,
            rag_status=rag_status,
        )
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        total_count_row = self.conn.execute(
            f"""
            SELECT COUNT(*) AS total_count
            FROM threads t
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS total_chunk_count,
                SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
                SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
                SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count,
                MAX(indexed_at) AS last_indexed_at
              FROM rag_chunks
              GROUP BY tid
            ) rag_stats ON rag_stats.tid = t.tid
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS live_job_count
              FROM jobs
              WHERE job_type = 'rag_index'
                AND status IN ('queued', 'running', 'retrying', 'cancel_requested', 'paused', 'interrupted')
              GROUP BY tid
            ) rag_jobs ON rag_jobs.tid = t.tid
            {where}
            """,
            args,
        ).fetchone()
        total_count = int(total_count_row["total_count"] or 0) if total_count_row is not None else 0
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        page = min(max(page, 1), total_pages)
        offset = (page - 1) * page_size
        backend = getattr(self.conn, "backend", None)
        order_clause = (
            (
                "COALESCE(rag_stats.last_indexed_at, TIMESTAMPTZ 'epoch') DESC, t.tid DESC"
                if backend in {"postgres", "postgresql"}
                else "COALESCE(rag_stats.last_indexed_at, '') DESC, t.tid DESC"
            )
            if index_state == "indexed"
            else (
                "CASE WHEN COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 1 ELSE 0 END ASC, "
                + (
                    "COALESCE(t.sync_time, TIMESTAMPTZ 'epoch')"
                    if backend in {"postgres", "postgresql"}
                    else "COALESCE(t.sync_time, '')"
                )
                + " DESC, t.tid DESC"
            )
        )
        rows = self.conn.execute(
            f"""
            SELECT
              t.tid,
              t.raw_title,
              t.display_title,
              t.publisher,
              t.sync_time,
              t.archive_status,
              t.forum_id,
              t.content_kind,
              t.category,
              tp.core_title_guess,
              tp.chapter_name,
              tp.author_guess,
              tp.group_name,
              COALESCE(rag_stats.chunk_count, 0) AS rag_chunk_count,
              COALESCE(rag_stats.indexed_chunk_count, 0) AS rag_indexed_chunk_count,
              COALESCE(rag_stats.pending_chunk_count, 0) AS rag_pending_chunk_count,
              COALESCE(rag_stats.failed_chunk_count, 0) AS rag_failed_chunk_count,
              rag_stats.last_indexed_at AS rag_last_indexed_at,
              CASE
                WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 AND COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 'indexing'
                WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN 'indexed'
                WHEN COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 'indexing'
                ELSE 'unindexed'
              END AS rag_index_state
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS chunk_count,
                SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
                SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
                SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count,
                MAX(indexed_at) AS last_indexed_at
              FROM rag_chunks
              GROUP BY tid
            ) rag_stats ON rag_stats.tid = t.tid
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS live_job_count
              FROM jobs
              WHERE job_type = 'rag_index'
                AND status IN ('queued', 'running', 'retrying', 'cancel_requested', 'paused', 'interrupted')
              GROUP BY tid
            ) rag_jobs ON rag_jobs.tid = t.tid
            {where}
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?
            """,
            [*args, page_size, offset],
        ).fetchall()
        return {
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "rows": rows,
        }

    def select_rag_thread_ids(
        self,
        *,
        tids: list[int] | None = None,
        q: str = "",
        forum_id: int | None = None,
        index_state: str = "unindexed",
        rag_status: str = "all",
    ) -> list[int]:
        if tids:
            placeholders = ",".join("?" for _ in tids)
            rows = self.conn.execute(
                f"SELECT tid FROM threads WHERE tid IN ({placeholders}) ORDER BY tid DESC",
                tids,
            ).fetchall()
            return [int(row["tid"]) for row in rows]

        filters, args = self._rag_thread_filter_clauses(
            q=q,
            forum_id=forum_id,
            index_state=index_state,
            rag_status=rag_status,
        )
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = self.conn.execute(
            f"""
            SELECT t.tid
            FROM threads t
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS chunk_count,
                SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
                SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
                SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count
              FROM rag_chunks
              GROUP BY tid
            ) rag_stats ON rag_stats.tid = t.tid
            LEFT JOIN (
              SELECT
                tid,
                COUNT(*) AS live_job_count
              FROM jobs
              WHERE job_type = 'rag_index'
                AND status IN ('queued', 'running', 'retrying', 'cancel_requested', 'paused', 'interrupted')
              GROUP BY tid
            ) rag_jobs ON rag_jobs.tid = t.tid
            {where}
            ORDER BY CASE WHEN COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 1 ELSE 0 END ASC, {"COALESCE(t.sync_time, TIMESTAMPTZ 'epoch')" if getattr(self.conn, "backend", None) in {"postgres", "postgresql"} else "COALESCE(t.sync_time, '')"} DESC, t.tid DESC
            LIMIT 200
            """,
            args,
        ).fetchall()
        return [int(row["tid"]) for row in rows]

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
        backend = getattr(self.conn, "backend", None)
        if backend in {"postgres", "postgresql"}:
            clauses: list[str] = []
            pg_params: dict[str, object] = {"limit": top_k}
            like_operator = self._like_operator()
            for idx, term in enumerate([term.strip() for term in query.split() if term.strip()]):
                key = f"pattern_{idx}"
                pg_params[key] = f"%{term}%"
                clauses.append(
                    f"(COALESCE(c.title, '') {like_operator} :{key} OR COALESCE(c.metadata_text, '') {like_operator} :{key} OR COALESCE(c.text, '') {like_operator} :{key})"
                )
            if not clauses:
                return []
            filters_sql = ""
            if forum_id is not None:
                filters_sql += " AND c.forum_id = :forum_id"
                pg_params["forum_id"] = forum_id
            if content_kind is not None:
                filters_sql += " AND c.content_kind = :content_kind"
                pg_params["content_kind"] = content_kind
            if tid is not None:
                filters_sql += " AND c.tid = :tid"
                pg_params["tid"] = tid
            if series_id is not None:
                filters_sql += " AND c.series_id = :series_id"
                pg_params["series_id"] = series_id
            if floor_start is not None:
                filters_sql += " AND (c.floor_no IS NULL OR c.floor_no >= :floor_start)"
                pg_params["floor_start"] = floor_start
            if floor_end is not None:
                filters_sql += " AND (c.floor_no IS NULL OR c.floor_no <= :floor_end)"
                pg_params["floor_end"] = floor_end
            return self.conn.execute(
                f"""
                SELECT c.*, 0.0 AS keyword_score
                FROM rag_chunks c
                WHERE {" AND ".join(clauses)}{filters_sql}
                ORDER BY c.floor_no IS NULL DESC, c.floor_no ASC, c.id ASC
                LIMIT :limit
                """,
                pg_params,
            ).fetchall()

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
        op = self._like_operator()
        term_sql = " AND ".join(f"(title {op} ? OR metadata_text {op} ? OR text {op} ?)" for _ in like_terms)
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
