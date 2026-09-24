from __future__ import annotations

import sqlite3


_FTS_SYNTAX_CHARS = set('"\'+-*^:(){}[]~')


def search_threads_sqlite(conn: sqlite3.Connection, query: str, *, limit: int = 50, forum_id: int | None = None) -> list[sqlite3.Row]:
    normalized = query.strip()
    if not normalized:
        return conn.execute(
            """
            SELECT
              t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
              t.archive_status, t.capture_mode, t.validation_status, t.context_path, t.series_id, t.export_path,
              t.forum_id, t.content_kind, t.category,
              tp.core_title_guess, tp.series_key, tp.chapter_name, tp.needs_review,
              (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
            FROM threads t
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            WHERE (? IS NULL OR t.forum_id = ?)
            ORDER BY COALESCE(t.sync_time, '') DESC, t.tid DESC
            LIMIT ?
            """,
            (forum_id, forum_id, limit),
        ).fetchall()

    keywords = [kw for kw in normalized.split() if kw]
    if any(not _is_fts_safe_keyword(keyword) for keyword in keywords):
        return _search_threads_like(conn, keywords, forum_id=forum_id, limit=limit)

    try:
        fts_terms = " AND ".join(keywords)
        fts_where = "WHERE thread_fts MATCH ?"
        params: list[object] = [fts_terms]
        if forum_id is not None:
            fts_where += " AND t.forum_id = ?"
            params.append(forum_id)
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT
              t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
              t.archive_status, t.capture_mode, t.validation_status, t.context_path, t.series_id, t.export_path,
              t.forum_id, t.content_kind, t.category,
              tp.core_title_guess, tp.series_key, tp.chapter_name, tp.needs_review,
              bm25(thread_fts) AS rank,
              (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
            FROM thread_fts
            JOIN threads t ON t.tid = thread_fts.tid
            LEFT JOIN title_parse tp ON tp.tid = t.tid
            {fts_where}
            ORDER BY rank
            LIMIT ?
            """,
            params,
        ).fetchall()
        if rows:
            return rows
    except sqlite3.OperationalError:
        pass

    return _search_threads_like(conn, keywords, forum_id=forum_id, limit=limit)


def _is_fts_safe_keyword(keyword: str) -> bool:
    return not any(ch in _FTS_SYNTAX_CHARS for ch in keyword)


def _search_threads_like(conn: sqlite3.Connection, keywords: list[str], *, forum_id: int | None, limit: int) -> list[sqlite3.Row]:
    or_cols = "(t.raw_title LIKE ? OR t.display_title LIKE ? OR t.publisher LIKE ? OR tp.core_title_guess LIKE ? OR tp.series_key LIKE ?)"
    and_parts = [f"({or_cols})" for _ in keywords]
    like_params: list[object] = []
    for kw in keywords:
        like = f"%{kw}%"
        like_params.extend([like, like, like, like, like])
    like_where = ""
    if forum_id is not None:
        like_where = " AND t.forum_id = ?"
        like_params.append(forum_id)
    like_params.append(limit)
    return conn.execute(
        f"""
        SELECT
          t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
          t.archive_status, t.capture_mode, t.validation_status, t.context_path, t.series_id, t.export_path,
          t.forum_id, t.content_kind, t.category,
          tp.core_title_guess, tp.series_key, tp.chapter_name, tp.needs_review,
          (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
        FROM threads t
        LEFT JOIN title_parse tp ON tp.tid = t.tid
        WHERE {" AND ".join(and_parts)}{like_where}
        ORDER BY COALESCE(t.sync_time, '') DESC, t.tid DESC
        LIMIT ?
        """,
        like_params,
    ).fetchall()
