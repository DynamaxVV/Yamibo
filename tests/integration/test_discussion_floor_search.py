from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.alembic_runner import _alembic_script_location
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.discussion_search import DiscussionSearchRepository


class _ExplainFloorConnection:
    """Capture the natural plan for the exact SQL emitted by the repository."""

    backend = "postgres"

    def __init__(self, conn):
        self.conn = conn
        self.floor_plan: list[str] | None = None
        self.title_plan: list[str] | None = None

    def execute(self, statement, parameters=None):
        if isinstance(statement, str) and "FROM threads t JOIN floors f" in statement:
            plan_rows = self.conn.execute(
                "EXPLAIN (COSTS OFF) " + statement,
                parameters,
            ).fetchall()
            self.floor_plan = [str(row[0]) for row in plan_rows]
        elif isinstance(statement, str) and "FROM threads t" in statement and "ILIKE" in statement:
            plan_rows = self.conn.execute(
                "EXPLAIN (COSTS OFF) " + statement,
                parameters,
            ).fetchall()
            self.title_plan = [str(row[0]) for row in plan_rows]
        return self.conn.execute(statement, parameters)


def test_postgres_broad_floor_search_is_indexed_and_filters_discussions_and_dates(pg_engine):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {
        "1", "true", "yes", "on"
    }:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    tid = 992_650_001
    non_discussion_tid = tid + 1
    with pg_engine.connect() as raw_conn:
        migrate(DatabaseConnection(raw_conn, backend="postgres"), schema="public")
    with pg_engine.begin() as raw_conn:
        raw_conn.execute(text("DELETE FROM floors WHERE tid IN (:tid, :other)"), {"tid": tid, "other": non_discussion_tid})
        raw_conn.execute(text("DELETE FROM threads WHERE tid IN (:tid, :other)"), {"tid": tid, "other": non_discussion_tid})
        raw_conn.execute(
            text("""
                INSERT INTO forums (forum_id, name, content_kind, base_url, enabled)
                VALUES (5, 'test discussion', 'discussion', 'https://example.invalid', TRUE)
                ON CONFLICT (forum_id) DO UPDATE SET content_kind = 'discussion', enabled = TRUE
            """)
        )
        raw_conn.execute(
            text("""
                INSERT INTO threads (tid, page_type, raw_title, display_title, pub_time,
                                     archive_status, validation_status, forum_id, content_kind)
                VALUES (:tid, 'discussion', '旧讨论', '旧讨论', '2020-01-01Z', 'complete', 'valid', 5, 'discussion'),
                       (:other, 'discussion', '非讨论内容', '非讨论内容', '2026-09-25Z', 'complete', 'valid', 5, 'comic')
            """),
            {"tid": tid, "other": non_discussion_tid},
        )
        raw_conn.execute(
            text("""
                INSERT INTO floors (pid, tid, floor_no, content, pub_time, has_images)
                VALUES (:pid, :tid, 1, '旧楼层', '2020-01-01Z', FALSE),
                       (:new_pid, :tid, 2, '这条回复含有罕见的索引验证短语', '2026-09-25Z', FALSE),
                       (:other_pid, :other, 1, '非讨论内容也含有罕见的索引验证短语', '2026-09-25Z', FALSE)
            """),
            {"tid": tid, "pid": tid + 10, "new_pid": tid + 11, "other": non_discussion_tid, "other_pid": tid + 12},
        )

        repo = DiscussionSearchRepository(DatabaseConnection(raw_conn, backend="postgres"))
        assert repo._floor_trigram_index_ready() is True
        result = repo.search(
            query="索引验证短语",
            forum_ids=[5],
            tids=[],
            start_at=None,
            end_at=None,
            limit=10,
            floor_match_limit=10,
        )
        assert result["body_search_status"] == "searched"
        assert [(item["tid"], item["pid"], item["matched_at"]) for item in result["items"]] == [
            (tid, tid + 11, "floor")
        ]

        dated = repo.search(
            query="索引验证短语",
            forum_ids=[5],
            tids=[],
            start_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
            limit=10,
            floor_match_limit=10,
        )
        assert [(item["tid"], item["pid"]) for item in dated["items"]] == [(tid, tid + 11)]

        no_hit = repo.search(
            query="完全不存在的片段",
            forum_ids=[5],
            tids=[],
            start_at=None,
            end_at=None,
            limit=10,
            floor_match_limit=10,
        )
        assert no_hit["body_search_status"] == "searched"
        assert no_hit["items"] == []


def test_pg_trgm_index_is_used_for_floors_while_short_terms_use_title_fallback(pg_engine):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {
        "1", "true", "yes", "on"
    }:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    tid = 992_651_001
    with pg_engine.connect() as raw_conn:
        migrate(DatabaseConnection(raw_conn, backend="postgres"), schema="public")
    with pg_engine.begin() as raw_conn:
        raw_conn.execute(text("DELETE FROM floors WHERE tid = :tid"), {"tid": tid})
        raw_conn.execute(text("DELETE FROM threads WHERE tid = :tid"), {"tid": tid})
        raw_conn.execute(
            text("""
                INSERT INTO threads (tid, page_type, raw_title, archive_status, validation_status,
                                     forum_id, content_kind)
                VALUES (:tid, 'discussion', 'plan test', 'complete', 'valid', 5, 'discussion')
            """), {"tid": tid}
        )
        raw_conn.execute(
            text("""
                INSERT INTO floors (pid, tid, floor_no, content, has_images)
                SELECT :pid_base + n, :tid, n,
                       CASE WHEN n = 45000 THEN '前缀更正说明后缀'
                           ELSE repeat('普通楼层内容用于执行计划测量。', 12) END,
                       FALSE
                FROM generate_series(1, 50000) AS n
            """), {"tid": tid, "pid_base": tid + 100}
        )
        raw_conn.execute(text("ANALYZE floors"))
        assert raw_conn.execute(
            text("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass('idx_floors_content_trgm')")
        ).scalar_one() is True
        explained_conn = _ExplainFloorConnection(DatabaseConnection(raw_conn, backend="postgres"))
        repo = DiscussionSearchRepository(explained_conn)
        result = repo.search(
            query="更正说明",
            forum_ids=[5],
            tids=[],
            start_at=None,
            end_at=None,
            limit=10,
            floor_match_limit=100,
        )
        assert result["body_search_status"] == "searched"
        assert explained_conn.floor_plan is not None
        three_char_plan = "\n".join(explained_conn.floor_plan)
        assert "Bitmap Index Scan on idx_floors_content_trgm" in three_char_plan

        explained_conn.floor_plan = None
        explained_conn.title_plan = None
        short_result = repo.search(
            query="百合",
            forum_ids=[5],
            tids=[],
            start_at=None,
            end_at=None,
            limit=10,
            floor_match_limit=100,
        )
        assert short_result["body_search_status"] == "INDEX_UNAVAILABLE"
        assert explained_conn.floor_plan is None
        assert explained_conn.title_plan is not None
        short_title_plan = "\n".join(explained_conn.title_plan)
        assert "threads t" in short_title_plan
        assert "JOIN floors" not in short_title_plan


def test_floor_trigram_migration_can_downgrade_and_upgrade_in_isolated_postgres(pg_engine):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {
        "1", "true", "yes", "on"
    }:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    with pg_engine.connect() as raw_conn:
        migrate(DatabaseConnection(raw_conn, backend="postgres"), schema="public")
        config = Config()
        config.set_main_option("script_location", str(_alembic_script_location()))
        config.attributes["connection"] = raw_conn
        config.attributes["schema"] = "public"

        command.downgrade(config, "021_thread_capture_mode")
        assert raw_conn.execute(
            text("SELECT to_regclass('idx_floors_content_trgm') IS NULL")
        ).scalar_one() is True

        command.upgrade(config, "head")
        assert raw_conn.execute(
            text("SELECT to_regclass('idx_floors_content_trgm') IS NOT NULL")
        ).scalar_one() is True
