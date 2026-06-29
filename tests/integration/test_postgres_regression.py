from __future__ import annotations

import pytest

from sqlalchemy import inspect, text

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.rebuild_search_indexes import rebuild_search_indexes
from yamibo_mcp.db.repositories.rag_vectors import get_vector_repository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.rag.scoring import normalize_vector_distance


def _cleanup_tid(raw_conn, tid: int) -> None:
    raw_conn.execute(text("DELETE FROM rag_chunks WHERE tid = :tid"), {"tid": tid})
    raw_conn.execute(text("DELETE FROM title_parse WHERE tid = :tid"), {"tid": tid})
    raw_conn.execute(text("DELETE FROM floors WHERE tid = :tid"), {"tid": tid})
    raw_conn.execute(text("DELETE FROM threads WHERE tid = :tid"), {"tid": tid})


def test_postgres_engine_fixture_connects(pg_engine):
    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1


def test_postgres_migration_creates_baseline_schema(pg_engine):
    with pg_engine.connect() as raw_conn:
        migrate(DatabaseConnection(raw_conn, backend="postgres"), schema="public")
        inspector = inspect(raw_conn)
        tables = set(inspector.get_table_names(schema="public"))

        assert {"forums", "threads", "jobs", "rag_chunks", "alembic_version"}.issubset(tables)

        thread_columns = {column["name"] for column in inspector.get_columns("threads", schema="public")}
        rag_columns = {column["name"] for column in inspector.get_columns("rag_chunks", schema="public")}
        thread_indexes = {index["name"] for index in inspector.get_indexes("threads", schema="public")}

        assert "search_vector" in thread_columns
        assert "content_preview" in thread_columns
        assert "embedding" in rag_columns
        assert "idx_threads_search_vector" in thread_indexes


def test_postgres_thread_search_uses_fts_and_ilike_fallback(pg_engine):
    with pg_engine.connect() as raw_conn:
        conn = DatabaseConnection(raw_conn, backend="postgres")
        migrate(conn, schema="public")
        _cleanup_tid(raw_conn, 1001)
        raw_conn.execute(text("DELETE FROM title_parse WHERE tid = 900001"))
        raw_conn.execute(text("DELETE FROM threads WHERE tid = 900001"))
        raw_conn.execute(
            text(
                """
                INSERT INTO threads (
                  tid, page_type, raw_title, display_title, publisher, pub_time, sync_time,
                  archive_status, validation_status, forum_id, content_kind, primary_media_type,
                  content_preview, search_vector
                ) VALUES (
                  900001, 'thread_detail', '测试轻小说 13话', '测试轻小说 13话', '汉化工房九九组',
                  TIMESTAMPTZ '2026-01-01 00:00:00+08', TIMESTAMPTZ '2026-01-01 01:00:00+08',
                  'complete', 'valid', 55, 'novel', 'text',
                  '少女在星空下告白。', to_tsvector('simple', '测试轻小说 13话 汉化工房九九组 少女在星空下告白')
                )
                """
            )
        )
        raw_conn.execute(
            text(
                """
                INSERT INTO title_parse (
                  tid, raw_title, display_title, group_name, author_guess,
                  core_title_guess, normalized_core_title, series_key, parser_version
                ) VALUES (
                  900001, '测试轻小说 13话', '测试轻小说 13话', '汉化工房九九组', NULL,
                  '测试轻小说', '测试轻小说', '测试轻小说', 'title-v1'
                )
                """
            )
        )
        repo = ThreadsRepository(conn)

        exact = repo.search_threads("测试轻小说 13话", limit=5)
        partial = repo.search_threads("汉化工房", limit=5)

        assert exact[0]["tid"] == 900001
        assert partial[0]["tid"] == 900001


def test_postgres_rebuild_search_indexes_handles_long_content(pg_engine):
    with pg_engine.connect() as raw_conn:
        conn = DatabaseConnection(raw_conn, backend="postgres")
        migrate(conn, schema="public")
        _cleanup_tid(raw_conn, 900003)
        raw_conn.execute(text("DELETE FROM title_parse WHERE tid = 900003"))
        raw_conn.execute(text("DELETE FROM threads WHERE tid = 900003"))
        raw_conn.execute(
            text(
                """
                INSERT INTO threads (
                  tid, page_type, raw_title, display_title, publisher, pub_time, sync_time,
                  archive_status, validation_status, forum_id, content_kind, primary_media_type
                ) VALUES (
                  900003, 'thread_detail', '超长内容测试', '超长内容测试', '作者',
                  TIMESTAMPTZ '2026-01-01 00:00:00+08', TIMESTAMPTZ '2026-01-01 01:00:00+08',
                  'complete', 'valid', 55, 'novel', 'text'
                )
                """
            )
        )
        raw_conn.execute(
            text(
                """
                INSERT INTO title_parse (
                  tid, raw_title, display_title, group_name, author_guess,
                  core_title_guess, normalized_core_title, series_key, parser_version
                ) VALUES (
                  900003, '超长内容测试', '超长内容测试', NULL, NULL,
                  '超长内容测试', '超长内容测试', '超长内容测试', 'title-v1'
                )
                """
            )
        )
        long_text = "长文本" * 150_000
        raw_conn.execute(
            text(
                """
                INSERT INTO floors (pid, tid, floor_no, publisher, content, pub_time, has_images)
                VALUES (900004, 900003, 1, '作者', :content, TIMESTAMPTZ '2026-01-01 01:00:00+08', 0)
                """
            ),
            {"content": long_text},
        )

        report = rebuild_search_indexes(conn)

        assert report["updated_threads"] >= 1
        row = raw_conn.execute(
            text("SELECT content_preview, search_vector IS NOT NULL AS has_search_vector FROM threads WHERE tid = 900003")
        ).one()
        assert row["content_preview"]
        assert row["has_search_vector"] is True


def test_postgres_vector_search_uses_pgvector_cosine_distance(pg_engine):
    with pg_engine.connect() as raw_conn:
        conn = DatabaseConnection(raw_conn, backend="postgres")
        migrate(conn, schema="public")
        _cleanup_tid(raw_conn, 1001)
        raw_conn.execute(text("DELETE FROM rag_chunks WHERE tid = 501"))
        raw_conn.execute(text("DELETE FROM title_parse WHERE tid = 501"))
        raw_conn.execute(text("DELETE FROM threads WHERE tid = 501"))
        raw_conn.execute(text("DELETE FROM rag_chunks WHERE tid = 900002"))
        raw_conn.execute(text("DELETE FROM title_parse WHERE tid = 900002"))
        raw_conn.execute(text("DELETE FROM threads WHERE tid = 900002"))
        raw_conn.execute(
            text(
                """
                INSERT INTO threads (
                  tid, page_type, raw_title, display_title, publisher, pub_time, sync_time,
                  archive_status, validation_status, forum_id, content_kind, primary_media_type
                ) VALUES (
                  900002, 'thread_detail', '向量测试', '向量测试', '作者',
                  TIMESTAMPTZ '2026-01-01 00:00:00+08', TIMESTAMPTZ '2026-01-01 01:00:00+08',
                  'complete', 'valid', 55, 'novel', 'text'
                )
                """
            )
        )
        row_id = raw_conn.execute(
            text(
                """
                INSERT INTO rag_chunks (
                  chunk_id, tid, pid, floor_no, chunk_type, forum_id, content_kind, series_id,
                  series_key, chapter_index, publisher, pub_time, title, metadata_text, text,
                  text_hash, source_uri, embedding_model, embedding_dimensions, embedding_status
                ) VALUES (
                  'thread:900002:title', 900002, NULL, NULL, 'thread_title', 55, 'novel', NULL,
                  NULL, NULL, '作者', TIMESTAMPTZ '2026-01-01 00:00:00+08', '向量测试', '作者',
                  '向量测试', 'hash-1', 'yamibo://threads/900002/summary', 'text-embedding-3-small', 3, 'pending'
                )
                RETURNING id
                """
            )
        ).scalar_one()
        vectors_repo = get_vector_repository(conn)
        embedding = [0.1, 0.2, 0.3] + [0.0] * 509
        vectors_repo.replace_embeddings([(int(row_id), embedding)])

        rows = vectors_repo.search(query_embedding=embedding, top_k=5)

        assert rows[0]["chunk_id"] == "thread:900002:title"
        assert rows[0]["vector_distance"] == pytest.approx(0.0)
        assert normalize_vector_distance(0.0, metric="cosine") == 1.0
        assert normalize_vector_distance(0.2, metric="cosine") == pytest.approx(0.9)
