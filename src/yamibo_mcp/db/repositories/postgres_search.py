from __future__ import annotations

from sqlalchemy import text

_POSTGRES_TSVECTOR_TEXT_LIMIT = 200_000


def search_threads_postgres(conn, query: str, *, limit: int = 50, forum_id: int | None = None):
    normalized = query.strip()
    if not normalized:
        return conn.execute(
            text(
                """
                SELECT
                  t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
                  t.archive_status, t.validation_status, t.context_path, t.series_id, t.export_path,
                  t.forum_id, t.content_kind, t.category,
                  tp.core_title_guess, tp.series_key, tp.chapter_name, tp.needs_review,
                  (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
                FROM threads t
                LEFT JOIN title_parse tp ON tp.tid = t.tid
                WHERE (:forum_id IS NULL OR t.forum_id = :forum_id)
                ORDER BY COALESCE(t.sync_time, TIMESTAMPTZ 'epoch') DESC, t.tid DESC
                LIMIT :limit
                """
            ),
            {"forum_id": forum_id, "limit": limit},
        ).fetchall()

    keywords = [kw for kw in normalized.split() if kw]
    if not keywords:
        return []

    or_clauses = []
    params: dict[str, object] = {"forum_id": forum_id, "limit": limit}
    for idx, keyword in enumerate(keywords):
        pattern_key = f"pattern_{idx}"
        params[pattern_key] = f"%{keyword}%"
        or_clauses.append(
            f"""(
                COALESCE(t.raw_title, '') ILIKE :{pattern_key}
                OR COALESCE(t.display_title, '') ILIKE :{pattern_key}
                OR COALESCE(t.publisher, '') ILIKE :{pattern_key}
                OR COALESCE(t.content_preview, '') ILIKE :{pattern_key}
                OR COALESCE(tp.core_title_guess, '') ILIKE :{pattern_key}
                OR COALESCE(tp.series_key, '') ILIKE :{pattern_key}
            )"""
        )

    stmt = text(
        f"""
        WITH q AS (
          SELECT plainto_tsquery('simple', :tsquery) AS tsquery
        )
        SELECT
          t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.sync_time,
          t.archive_status, t.validation_status, t.context_path, t.series_id, t.export_path,
          t.forum_id, t.content_kind, t.category,
          tp.core_title_guess, tp.series_key, tp.chapter_name, tp.needs_review,
          ts_rank(
            COALESCE(
              t.search_vector,
              to_tsvector(
                'simple',
                left(
                  concat_ws(
                    ' ',
                    COALESCE(t.raw_title, ''),
                    COALESCE(t.display_title, ''),
                    COALESCE(t.publisher, ''),
                    COALESCE(t.content_preview, ''),
                    COALESCE(tp.core_title_guess, ''),
                    COALESCE(tp.series_key, '')
                  ),
                  :tsvector_text_limit
                )
              )
            ),
            q.tsquery
          ) AS rank,
          (SELECT COUNT(*) FROM floors f WHERE f.tid = t.tid) AS reply_count
        FROM threads t
        LEFT JOIN title_parse tp ON tp.tid = t.tid
        CROSS JOIN q
        WHERE (CAST(:forum_id AS INTEGER) IS NULL OR t.forum_id = :forum_id)
          AND (
            q.tsquery @@ COALESCE(
              t.search_vector,
              to_tsvector(
                'simple',
                left(
                  concat_ws(
                    ' ',
                    COALESCE(t.raw_title, ''),
                    COALESCE(t.display_title, ''),
                    COALESCE(t.publisher, ''),
                    COALESCE(t.content_preview, ''),
                    COALESCE(tp.core_title_guess, ''),
                    COALESCE(tp.series_key, '')
                  ),
                  :tsvector_text_limit
                )
              )
            )
          OR {" OR ".join(or_clauses)}
        )
        ORDER BY rank DESC, COALESCE(t.sync_time, TIMESTAMPTZ 'epoch') DESC, t.tid DESC
        LIMIT :limit
        """
    )
    return conn.execute(
        stmt,
        params | {"tsquery": normalized, "tsvector_text_limit": _POSTGRES_TSVECTOR_TEXT_LIMIT},
    ).fetchall()
