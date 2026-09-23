from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from yamibo_mcp.db.migrations import migrate


class TestMigrationCreatesNewTables:
    def test_empty_db_migration_creates_forums(self, db):
        # Arrange & Act — db fixture already runs migrate
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        # Assert
        assert "forums" in tables

    def test_empty_db_migration_creates_content_blocks(self, db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "content_blocks" in tables

    def test_empty_db_migration_creates_assets(self, db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "assets" in tables

    def test_empty_db_migration_creates_rag_tables(self, db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "rag_index_meta" in tables
        assert "rag_chunks" in tables
        assert "rag_chunks_fts" in tables

    def test_forums_seeded_with_defaults(self, db):
        rows = db.execute("SELECT * FROM forums ORDER BY forum_id").fetchall()
        assert len(rows) >= 4
        forum_ids = [r["forum_id"] for r in rows]
        assert 30 in forum_ids
        assert 55 in forum_ids
        assert 5 in forum_ids
        assert 33 in forum_ids

    def test_comic_forum_has_correct_content_kind(self, db):
        row = db.execute("SELECT * FROM forums WHERE forum_id = 30").fetchone()
        assert row["content_kind"] == "comic"
        assert row["name"] == "漫画区"
        assert row["name_en"] == "Comic"

    def test_novel_forum_has_correct_content_kind(self, db):
        row = db.execute("SELECT * FROM forums WHERE forum_id = 55").fetchone()
        assert row["content_kind"] == "novel"


class TestMigrationAddsThreadColumns:
    def test_threads_has_forum_id_column(self, db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(threads)").fetchall()}
        assert "forum_id" in columns

    def test_threads_has_content_kind_column(self, db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(threads)").fetchall()}
        assert "content_kind" in columns

    def test_threads_has_primary_media_type_column(self, db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(threads)").fetchall()}
        assert "primary_media_type" in columns

    def test_threads_has_forum_tid_scan_index(self, db):
        indexes = db.execute("PRAGMA index_list(threads)").fetchall()
        forum_tid_index = next(row for row in indexes if row[1] == "idx_threads_forum_tid")
        assert forum_tid_index[4] == 1

    def test_assets_has_floor_image_match_index(self, db):
        indexes = {row[1] for row in db.execute("PRAGMA index_list(assets)").fetchall()}
        assert "idx_assets_tid_pid_type" in indexes


class TestMigrationBackfill:
    def test_removed_superseded_jobs_are_cleaned(self, db):
        db.execute(
            "INSERT INTO jobs (job_id, job_type, payload_json, status) VALUES (?, ?, ?, ?)",
            ("legacy-superseded", "noop", "{}", "superseded"),
        )
        db.execute(
            "INSERT INTO job_events (job_id, event_type, status) VALUES (?, ?, ?)",
            ("legacy-superseded", "job.superseded", "superseded"),
        )
        db.commit()

        migrate(db)

        assert db.execute("SELECT COUNT(*) FROM jobs WHERE status = 'superseded'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM job_events WHERE job_id = 'legacy-superseded'").fetchone()[0] == 0

    def test_existing_threads_backfilled_forum_id(self, db):
        # Arrange — insert a thread via raw SQL (simulating old schema data)
        db.execute(
            "INSERT INTO threads (tid, page_type, raw_title, display_title, archive_status, validation_status) "
            "VALUES (99999, 'comic', 'test', 'test', 'stale', 'unknown')"
        )
        db.commit()
        # Act — re-run migrate to trigger backfill
        migrate(db)
        # Assert
        row = db.execute("SELECT * FROM threads WHERE tid = 99999").fetchone()
        assert row["forum_id"] == 30
        assert row["content_kind"] == "comic"
        assert row["primary_media_type"] == "image"

    def test_new_threads_keep_explicit_forum_id(self, db):
        db.execute(
            "INSERT INTO threads (tid, page_type, raw_title, display_title, archive_status, validation_status, forum_id, content_kind, primary_media_type) "
            "VALUES (88888, 'comic', 'test', 'test', 'stale', 'unknown', 55, 'novel', 'text')"
        )
        db.commit()
        migrate(db)
        row = db.execute("SELECT * FROM threads WHERE tid = 88888").fetchone()
        assert row["forum_id"] == 55
        assert row["content_kind"] == "novel"


class TestMigrationIdempotency:
    def test_double_migrate_stable_schema(self, db):
        # Arrange
        tables_before = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        # Act
        migrate(db)
        migrate(db)
        # Assert
        tables_after = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert tables_before == tables_after

    def test_double_migrate_stable_forum_rows(self, db):
        migrate(db)
        migrate(db)
        rows = db.execute("SELECT COUNT(*) FROM forums").fetchone()[0]
        assert rows >= 4

    def test_double_migrate_stable_columns(self, db):
        columns_before = {row[1] for row in db.execute("PRAGMA table_info(threads)").fetchall()}
        migrate(db)
        columns_after = {row[1] for row in db.execute("PRAGMA table_info(threads)").fetchall()}
        assert columns_before == columns_after

    def test_jobs_parent_created_index_exists(self, db):
        indexes = {row["name"] for row in db.execute("PRAGMA index_list(jobs)").fetchall()}
        assert "idx_jobs_parent_created" in indexes


class TestOldSchemaMigration:
    def test_old_db_without_new_columns_migrates_cleanly(self):
        # Arrange — create a DB with old schema only
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE threads (
              tid INTEGER PRIMARY KEY, series_id INTEGER, page_type TEXT NOT NULL DEFAULT 'unknown',
              raw_title TEXT NOT NULL, display_title TEXT, publisher TEXT, publisher_uid TEXT,
              pub_time TEXT, sync_time TEXT, last_pid INTEGER,
              permission INTEGER NOT NULL DEFAULT 0, is_finished INTEGER NOT NULL DEFAULT 0,
              image_count INTEGER NOT NULL DEFAULT 0, context_path TEXT,
              archive_status TEXT NOT NULL DEFAULT 'stale', validation_status TEXT NOT NULL DEFAULT 'unknown',
              validation_errors_json TEXT, missing_images_json TEXT,
              needs_title_review INTEGER NOT NULL DEFAULT 0, needs_series_review INTEGER NOT NULL DEFAULT 0,
              is_exported INTEGER NOT NULL DEFAULT 0, export_path TEXT
            );
            INSERT INTO threads (tid, page_type, raw_title, display_title) VALUES (100, 'comic', 'old thread', 'old thread');
        """)
        conn.commit()
        # Act
        migrate(conn)
        # Assert
        row = conn.execute("SELECT * FROM threads WHERE tid = 100").fetchone()
        assert row["forum_id"] == 30
        assert row["content_kind"] == "comic"
        assert row["primary_media_type"] == "image"
        forums = conn.execute("SELECT COUNT(*) FROM forums").fetchone()[0]
        assert forums >= 4
        conn.close()


def test_postgres_migration_dispatches_to_alembic(monkeypatch):
    from yamibo_mcp.db import migrations as migrations_module

    called = {}

    def fake_upgrade(connection, *, schema="public"):
        called["connection"] = connection
        called["schema"] = schema

    monkeypatch.setattr(migrations_module, "upgrade_postgres_schema", fake_upgrade)

    class FakePostgresConnection:
        backend = "postgres"

        def __init__(self):
            self.raw_connection = object()

    migrate(FakePostgresConnection(), schema="yamibo_test")

    assert called["schema"] == "yamibo_test"
    assert called["connection"] is not None


def test_postgres_schema_bootstrap_is_serialized(monkeypatch):
    from yamibo_mcp.db import alembic_runner

    alembic_runner._BOOTSTRAPPED_POSTGRES_DATABASES.clear()
    calls: list[float] = []
    active = {"count": 0, "max": 0}
    lock = threading.Lock()

    def fake_upgrade(config, revision):
        with lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        time.sleep(0.05)
        with lock:
            active["count"] -= 1
        calls.append(time.monotonic())

    monkeypatch.setattr(alembic_runner.command, "upgrade", fake_upgrade)

    class FakeRawConnection:
        def __init__(self):
            self.engine = type("Engine", (), {"url": type("URL", (), {"render_as_string": lambda self, hide_password=True: "postgresql://test"})()})()

        def execute(self, *args, **kwargs):
            return self

        def commit(self):
            return None

    raw = FakeRawConnection()

    def worker():
        alembic_runner.upgrade_postgres_schema(raw, schema="public")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert active["max"] == 1
