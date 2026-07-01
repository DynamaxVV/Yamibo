from __future__ import annotations

from datetime import date

import pytest

from sqlalchemy import inspect, text

from yamibo_mcp.application.discussion_report import (
    generate_forum_research_report,
    generate_trend_report,
)
from yamibo_mcp.application.discussion_trend_builder import BuildInput, build_trend_mart
from yamibo_mcp.application.discussion_trend_queries import (
    get_discussion_partition_trends,
    get_discussion_report,
    get_discussion_topic_evidence,
    get_discussion_topic_trends,
    get_discussion_user_trends,
    get_forum_evidence_pack,
)
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.discussion_trends import DiscussionTrendRepository, ensure_postgres
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
        assert {"discussion_index_runs", "discussion_current_indexes", "discussion_topics", "discussion_topic_assignments", "discussion_partition_daily", "discussion_topic_daily", "discussion_user_daily", "discussion_report_runs", "discussion_rag_chunk_topics"}.issubset(tables)

        thread_columns = {column["name"] for column in inspector.get_columns("threads", schema="public")}
        rag_columns = {column["name"] for column in inspector.get_columns("rag_chunks", schema="public")}
        trend_columns = {column["name"] for column in inspector.get_columns("discussion_current_indexes", schema="public")}
        current_pk = inspector.get_pk_constraint("discussion_current_indexes", schema="public")
        run_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("discussion_index_runs", schema="public")
        }
        topic_assign_indexes = {index["name"] for index in inspector.get_indexes("discussion_topic_assignments", schema="public")}
        thread_indexes = {index["name"] for index in inspector.get_indexes("threads", schema="public")}
        floor_indexes = {index["name"] for index in inspector.get_indexes("floors", schema="public")}

        assert "search_vector" in thread_columns
        assert "content_preview" in thread_columns
        assert "embedding" in rag_columns
        assert "is_current" not in trend_columns
        assert current_pk["constrained_columns"] == ["forum_id", "start_date", "end_date", "version"]
        assert ("forum_id", "start_date", "end_date", "version") not in run_uniques
        assert "idx_threads_search_vector" in thread_indexes
        assert "idx_threads_forum_pub_time" in thread_indexes
        assert "idx_floors_tid_pub_time" in floor_indexes
        assert "idx_discussion_topic_assignments_run_topic" in topic_assign_indexes


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
                VALUES (900004, 900003, 1, '作者', :content, TIMESTAMPTZ '2026-01-01 01:00:00+08', :has_images)
                """
            ),
            {"content": long_text, "has_images": False},
        )

        report = rebuild_search_indexes(conn)

        assert report["updated_threads"] >= 1
        row = raw_conn.execute(
            text("SELECT content_preview, search_vector IS NOT NULL AS has_search_vector FROM threads WHERE tid = 900003")
        ).one()
        assert row._mapping["content_preview"]
        assert row._mapping["has_search_vector"] is True


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


def test_discussion_trend_repository_lifecycle_and_guardrails(pg_engine):
    with pg_engine.connect() as raw_conn:
        conn = DatabaseConnection(raw_conn, backend="postgres")
        migrate(conn, schema="public")
        repo = DiscussionTrendRepository(conn)

        repo.acquire_window_lock(forum_id=5, start_date="2024-01-01", end_date="2024-01-31", version="v1")
        run = repo.create_run(
            run_id="trend-run-1",
            forum_id=5,
            start_date="2024-01-01",
            end_date="2024-01-31",
            version="v1",
            status="running",
        )
        assert run.run_id == "trend-run-1"

        repo.set_current_run(
            forum_id=5,
            start_date="2024-01-01",
            end_date="2024-01-31",
            version="v1",
            current_run_id="trend-run-1",
        )
        current = repo.get_current_run(forum_id=5, start_date="2024-01-01", end_date="2024-01-31", version="v1")
        assert current is not None
        assert current.run_id == "trend-run-1"

        succeeded = repo.mark_run_succeeded("trend-run-1", metrics_json={"rows": 1}, warnings_json=["rag missing"])
        assert succeeded.status == "succeeded"
        assert succeeded.metrics_json["rows"] == 1
        assert succeeded.warnings_json == ["rag missing"]

        failed = repo.create_run(
            run_id="trend-run-2",
            forum_id=5,
            start_date="2024-02-01",
            end_date="2024-02-28",
            version="v1",
            status="running",
        )
        assert failed.status == "running"
        repo.mark_run_failed("trend-run-2", error_json={"code": "boom"})
        assert repo.get_run("trend-run-2").status == "failed"


def test_discussion_trend_repository_rejects_sqlite_backend():
    class Conn:
        backend = "sqlite"

    with pytest.raises(ValueError, match="discussion trends require postgres backend"):
        ensure_postgres(Conn())


def test_discussion_trend_builder_end_to_end_on_synthetic_pg(pg_engine):
    """Step 02: end-to-end builder run on synthetic PG data → all five mart tables populated."""
    from datetime import date, datetime, timezone
    from yamibo_mcp.application.discussion_trend_builder import (
        BuildInput,
        build_trend_mart,
    )

    with pg_engine.connect() as raw_conn:
        conn = DatabaseConnection(raw_conn, backend="postgres")
        migrate(conn, schema="public")
        # clean any prior fixtures
        raw_conn.execute(text("DELETE FROM discussion_topic_daily WHERE run_id = 'builder-e2e'"))
        raw_conn.execute(text("DELETE FROM discussion_user_daily WHERE run_id = 'builder-e2e'"))
        raw_conn.execute(text("DELETE FROM discussion_partition_daily WHERE run_id = 'builder-e2e'"))
        raw_conn.execute(text("DELETE FROM discussion_topic_assignments WHERE run_id = 'builder-e2e'"))
        raw_conn.execute(text("DELETE FROM discussion_topics WHERE run_id = 'builder-e2e'"))
        raw_conn.execute(text("DELETE FROM discussion_current_indexes WHERE current_run_id = 'builder-e2e'"))
        raw_conn.execute(text("DELETE FROM discussion_index_runs WHERE run_id = 'builder-e2e'"))
        # synthetic threads + floors
        for tid in (800001, 800002, 800003):
            raw_conn.execute(text("DELETE FROM floors WHERE tid = :tid"), {"tid": tid})
            raw_conn.execute(text("DELETE FROM title_parse WHERE tid = :tid"), {"tid": tid})
            raw_conn.execute(text("DELETE FROM threads WHERE tid = :tid"), {"tid": tid})
            raw_conn.execute(
                text(
                    """
                    INSERT INTO threads (
                      tid, page_type, raw_title, display_title, publisher, pub_time, sync_time,
                      archive_status, validation_status, forum_id, content_kind, primary_media_type,
                      category
                    ) VALUES (
                      :tid, 'thread_detail', :raw_title, :raw_title, 'u' || CAST(:tid AS text),
                      TIMESTAMPTZ '2024-01-10 00:00:00+08', TIMESTAMPTZ '2024-01-10 01:00:00+08',
                      'complete', 'valid', 5, 'discussion', 'text', 'anime'
                    )
                    """
                ),
                {"tid": tid, "raw_title": f"测试轻小说 tid{tid}"},
            )
            for floor_no in range(1, 8):
                raw_conn.execute(
                    text(
                        """
                        INSERT INTO floors (
                          pid, tid, floor_no, publisher, publisher_uid, content, pub_time, has_images
                        ) VALUES (
                          :pid, :tid, :floor_no, :pub, :uid, '', TIMESTAMPTZ '2024-01-10 00:00:00+08' + (INTERVAL '1 hour' * :floor_no), false
                        )
                        """
                    ),
                    {
                        "pid": tid * 100 + floor_no,
                        "tid": tid,
                        "floor_no": floor_no,
                        "pub": f"u{tid}-{floor_no}",
                        "uid": f"uid{tid}-{floor_no}",
                    },
                )
            raw_conn.execute(
                text(
                    """
                    INSERT INTO title_parse (
                      tid, raw_title, display_title, normalized_core_title, parser_version
                    ) VALUES (
                      :tid, :raw_title, :raw_title, '测试轻小说', 'title-v1'
                    )
                    """
                ),
                {"tid": tid, "raw_title": f"测试轻小说 tid{tid}"},
            )

        payload = BuildInput(
            forum_id=5,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            version="trend-v1",
            manual_seed_topics=(("anime", "动漫综合"),),
        )
        result = build_trend_mart(conn, run_id="builder-e2e", payload=payload)
        assert result.status == "succeeded"
        assert "TOPIC_QUALITY_INSUFFICIENT" not in result.warnings
        # verify row counts
        partition_rows = raw_conn.execute(
            text("SELECT COUNT(*) AS c FROM discussion_partition_daily WHERE run_id = :r"),
            {"r": "builder-e2e"},
        ).scalar_one()
        assert partition_rows == 1
        user_rows = raw_conn.execute(
            text("SELECT COUNT(*) AS c FROM discussion_user_daily WHERE run_id = :r"),
            {"r": "builder-e2e"},
        ).scalar_one()
        assert user_rows >= 5
        topic_rows = raw_conn.execute(
            text("SELECT COUNT(*) AS c FROM discussion_topics WHERE run_id = :r"),
            {"r": "builder-e2e"},
        ).scalar_one()
        assert topic_rows >= 1
        assign_rows = raw_conn.execute(
            text("SELECT COUNT(*) AS c FROM discussion_topic_assignments WHERE run_id = :r"),
            {"r": "builder-e2e"},
        ).scalar_one()
        assert assign_rows >= 3
        topic_daily_rows = raw_conn.execute(
            text("SELECT COUNT(*) AS c FROM discussion_topic_daily WHERE run_id = :r"),
            {"r": "builder-e2e"},
        ).scalar_one()
        assert topic_daily_rows >= 1
        # current_run_id is set
        current_run_id = raw_conn.execute(
            text(
                """
                SELECT current_run_id FROM discussion_current_indexes
                WHERE forum_id = :f AND start_date = :s AND end_date = :e AND version = :v
                """
            ),
            {"f": 5, "s": "2024-01-01", "e": "2024-01-31", "v": "trend-v1"},
        ).scalar_one()
        assert current_run_id == "builder-e2e"


def test_discussion_trend_smoke_queries_reports_and_sql_fallback(pg_engine):
    from unittest.mock import MagicMock, patch

    forum_id = 33
    run_id = "discussion-smoke"
    start_date = "2014-11-01"
    end_date = "2014-11-30"
    tids = (810001, 810002, 810003)

    with pg_engine.connect() as raw_conn:
        conn = DatabaseConnection(raw_conn, backend="postgres")
        migrate(conn, schema="public")
        repo = DiscussionTrendRepository(conn)

        raw_conn.execute(text("DELETE FROM discussion_report_runs WHERE run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_rag_chunk_topics WHERE run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_topic_daily WHERE run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_user_daily WHERE run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_partition_daily WHERE run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_topic_assignments WHERE run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_topics WHERE run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_current_indexes WHERE current_run_id = :run_id"), {"run_id": run_id})
        raw_conn.execute(text("DELETE FROM discussion_index_runs WHERE run_id = :run_id"), {"run_id": run_id})

        for tid in tids:
            raw_conn.execute(text("DELETE FROM floors WHERE tid = :tid"), {"tid": tid})
            raw_conn.execute(text("DELETE FROM title_parse WHERE tid = :tid"), {"tid": tid})
            raw_conn.execute(text("DELETE FROM threads WHERE tid = :tid"), {"tid": tid})
            raw_conn.execute(
                text(
                    """
                    INSERT INTO threads (
                      tid, page_type, raw_title, display_title, publisher, pub_time, sync_time,
                      archive_status, validation_status, forum_id, content_kind, primary_media_type, category
                    ) VALUES (
                      :tid, 'thread_detail', :title, :title, :publisher,
                      TIMESTAMPTZ '2014-11-10 00:00:00+08', TIMESTAMPTZ '2014-11-10 01:00:00+08',
                      'complete', 'valid', :forum_id, 'discussion', 'text', 'sea'
                    )
                    """
                ),
                {
                    "tid": tid,
                    "title": f"海域 黑话 测试 tid{tid}",
                    "publisher": f"user_{tid}",
                    "forum_id": forum_id,
                },
            )
            raw_conn.execute(
                text(
                    """
                    INSERT INTO title_parse (
                      tid, raw_title, display_title, normalized_core_title, parser_version
                    ) VALUES (
                      :tid, :title, :title, '海域黑话测试', 'title-v1'
                    )
                    """
                ),
                {"tid": tid, "title": f"海域 黑话 测试 tid{tid}"},
            )
            for floor_no in range(1, 8):
                raw_conn.execute(
                    text(
                        """
                        INSERT INTO floors (
                          pid, tid, floor_no, publisher, publisher_uid, content, pub_time, has_images, quote_text, reply_text
                        ) VALUES (
                          :pid, :tid, :floor_no, :publisher, :uid, :content,
                          TIMESTAMPTZ '2014-11-10 00:00:00+08' + (INTERVAL '1 hour' * :floor_no),
                          false, :quote_text, :reply_text
                        )
                        """
                    ),
                    {
                        "pid": tid * 100 + floor_no,
                        "tid": tid,
                        "floor_no": floor_no,
                        "publisher": f"user_{tid}_{floor_no}",
                        "uid": f"uid_{tid}_{floor_no}",
                        "content": f"海域 黑话 讨论 floor {floor_no} tid {tid}",
                        "quote_text": "黑话引用" if floor_no % 2 == 0 else None,
                        "reply_text": "氛围回复" if floor_no % 3 == 0 else None,
                    },
                )

        payload = BuildInput(
            forum_id=forum_id,
            start_date=date(2014, 11, 1),
            end_date=date(2014, 11, 30),
            version="trend-v1",
            manual_seed_topics=(("sea", "海域综合"),),
        )
        build_result = build_trend_mart(conn, run_id=run_id, payload=payload)
        assert build_result.status == "succeeded"

        run = repo.get_current_run(
            forum_id=forum_id,
            start_date=start_date,
            end_date=end_date,
            version="trend-v1",
        )
        assert run is not None

        trend_report_json, trend_markdown = generate_trend_report(
            repo=repo,
            run=run,
            forum_id=forum_id,
            start_date=start_date,
            end_date=end_date,
            version="trend-v1",
            period="monthly",
            conn=conn,
        )
        repo.insert_report_run(
            run_id=run.run_id,
            report_kind="trend_report",
            report_json=trend_report_json,
            report_markdown=trend_markdown,
        )

        settings = MagicMock()
        settings.db_path = "postgres://test"

        def _connect(_db_path):
            return DatabaseConnection(pg_engine.connect(), backend="postgres")

        with patch("yamibo_mcp.application.discussion_trend_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.discussion_trend_queries.connect", side_effect=_connect):
            partition = get_discussion_partition_trends(
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
            )
            topics = get_discussion_topic_trends(
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
            )
            users = get_discussion_user_trends(
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
            )
            topic_id = topics.data["topics"][0]["topic_id"]
            topic_evidence = get_discussion_topic_evidence(
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
                topic_id=topic_id,
                mode="sql",
            )
            forum_pack = get_forum_evidence_pack(
                forum_id=forum_id,
                query="黑话",
                start_date=start_date,
                end_date=end_date,
                intent="slang_usage",
                mode="sql",
            )
            report_result = get_discussion_report(
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
                report_kind="trend_report",
                format="json",
            )

        assert partition.ok is True
        assert partition.data["buckets"]
        assert topics.ok is True
        assert topics.data["topics"]
        assert users.ok is True
        assert users.data["users"]
        assert topic_evidence.ok is True
        assert topic_evidence.data["retrieval_mode"] == "sql"
        assert topic_evidence.data["items"]
        assert forum_pack.ok is True
        assert forum_pack.data["retrieval_mode"] == "sql"
        assert forum_pack.data["items"]
        assert report_result.ok is True
        assert report_result.data["reports"][0]["report_kind"] == "trend_report"

        with patch("yamibo_mcp.application.discussion_trend_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.discussion_trend_queries.connect", side_effect=_connect):
            research_json, research_markdown = generate_forum_research_report(
                repo=repo,
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
                question="海域区这个时期的黑话用法如何？",
                intent="slang_usage",
                evidence_query="黑话",
                run=run,
                conn=conn,
            )

        assert research_json["evidence_summary"]["items"]
        assert research_json["trend_context"] is not None
        assert "论坛研究报告" in research_markdown
