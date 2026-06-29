from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

_POSTGRES_TSVECTOR_TEXT_LIMIT = 200_000
_POSTGRES_TSVECTOR_MAX_BYTES = 1_048_575


def find_overlong_threads(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            WITH floor_bytes AS (
              SELECT
                f.tid,
                SUM(octet_length(COALESCE(f.content, ''))) + GREATEST(COUNT(*) - 1, 0) AS floor_bytes
              FROM floors f
              GROUP BY f.tid
            ),
            thread_bytes AS (
              SELECT
                t.tid,
                COALESCE(t.raw_title, '') AS raw_title,
                COALESCE(t.display_title, '') AS display_title,
                COALESCE(t.publisher, '') AS publisher,
                COALESCE(tp.core_title_guess, '') AS core_title_guess,
                COALESCE(tp.series_key, '') AS series_key,
                COALESCE(floor_bytes.floor_bytes, 0) AS floor_bytes,
                octet_length(
                  concat_ws(
                    ' ',
                    COALESCE(t.raw_title, ''),
                    COALESCE(t.display_title, ''),
                    COALESCE(t.publisher, ''),
                    COALESCE(tp.core_title_guess, ''),
                    COALESCE(tp.series_key, '')
                  )
                ) AS header_bytes
              FROM threads t
              JOIN title_parse tp ON tp.tid = t.tid
              LEFT JOIN floor_bytes ON floor_bytes.tid = t.tid
            )
            SELECT
              tid,
              raw_title,
              display_title,
              publisher,
              core_title_guess,
              series_key,
              header_bytes + floor_bytes AS estimated_bytes
            FROM thread_bytes
            WHERE header_bytes + floor_bytes > :max_bytes
            ORDER BY estimated_bytes DESC, tid DESC
            """
        ),
        {"max_bytes": _POSTGRES_TSVECTOR_MAX_BYTES},
    ).fetchall()
    return [
        {
            "tid": row["tid"],
            "raw_title": row["raw_title"],
            "display_title": row["display_title"],
            "publisher": row["publisher"],
            "core_title_guess": row["core_title_guess"],
            "series_key": row["series_key"],
            "estimated_bytes": row["estimated_bytes"],
        }
        for row in rows
    ]


def rebuild_search_indexes(conn) -> dict[str, int]:
    backend = getattr(conn, "backend", None)
    if backend not in {"postgres", "postgresql"}:
        raise ValueError("rebuild_search_indexes is only supported on PostgreSQL")

    overlong_threads = find_overlong_threads(conn)
    if overlong_threads:
        logger.warning("发现 %s 条超长贴子超过 1MB tsvector 限制", len(overlong_threads))
        for row in overlong_threads[:20]:
            logger.warning(
                "超长贴子 tid=%s bytes=%s title=%s",
                row["tid"],
                row["estimated_bytes"],
                row["display_title"] or row["raw_title"],
            )

    updated_threads = conn.execute(
        text(
            """
            UPDATE threads t
            SET content_preview = COALESCE(
                  (
                    SELECT left(string_agg(COALESCE(f.content, ''), ' ' ORDER BY f.floor_no, f.pid), 2000)
                    FROM floors f
                    WHERE f.tid = t.tid
                  ),
                  ''
                ),
                search_vector = to_tsvector(
                  'simple',
                  left(
                    concat_ws(
                      ' ',
                      COALESCE(t.raw_title, ''),
                      COALESCE(t.display_title, ''),
                      COALESCE(t.publisher, ''),
                      COALESCE(tp.core_title_guess, ''),
                      COALESCE(tp.series_key, ''),
                      COALESCE(
                        (
                          SELECT string_agg(COALESCE(f.content, ''), ' ' ORDER BY f.floor_no, f.pid)
                          FROM floors f
                          WHERE f.tid = t.tid
                        ),
                        ''
                      )
                    ),
                    :tsvector_text_limit
                  )
                )
            FROM title_parse tp
            WHERE tp.tid = t.tid
            """
        ),
        {"tsvector_text_limit": _POSTGRES_TSVECTOR_TEXT_LIMIT},
    ).rowcount or 0

    missing_embeddings = conn.execute(
        text("SELECT COUNT(*) AS c FROM rag_chunks WHERE embedding IS NULL")
    ).scalar_one()
    conn.commit()
    return {
        "updated_threads": int(updated_threads),
        "missing_embeddings": int(missing_embeddings or 0),
        "overlong_threads": overlong_threads,
        "overlong_thread_count": len(overlong_threads),
    }
