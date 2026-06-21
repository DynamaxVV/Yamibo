from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from yamibo_mcp.application.archive_commands import archive_thread_job
from yamibo_mcp.application.legacy_use_cases import ensure_thread
from yamibo_mcp.db.repositories.threads import ThreadsRepository


_THREAD_SUMMARY_KEYS = {
    "tid", "url", "display_title", "raw_title", "publisher", "pub_time",
    "core_title", "chapter_name", "chapter_title", "series_id", "series_key",
    "archive_status", "validation_status", "sync_time", "export_path", "resources",
}

_THREAD_DETAIL_EXTRA_KEYS = _THREAD_SUMMARY_KEYS | {
    "publisher_uid", "image_count", "context_path", "export_path_abs",
    "title_parse", "floors", "floor_count",
}


def _fake_settings(tmp_path: Path):
    settings = MagicMock()
    settings.db_path = str(tmp_path / "test.db")
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.worker_lease_seconds = 300
    settings.cookie_file = tmp_path / "cookies.txt"
    settings.use_system_proxy = False
    settings.login_username = None
    settings.login_password = None
    settings.project_root = tmp_path
    settings.image_download_timeout_seconds = 10
    settings.image_download_retries = 1
    return settings


def _seed_thread(conn: sqlite3.Connection, tid: int = 100) -> None:
    from yamibo_mcp.db.repositories.series import SeriesRepository
    from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot

    title = TitleSnapshot(
        raw_title="[TestGroup] Test Title [ch01]",
        display_title="Test Title",
        group_name="TestGroup",
        author_guess=None,
        core_title_guess="Test Title",
        normalized_core_title="testtitle",
        series_key="testtitle",
        title_aliases=[],
        chapter_name="ch01",
        chapter_index=1,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.9,
        needs_review=False,
        parser_version="title-v1",
    )
    floor = FloorSnapshot(
        pid=1001,
        tid=tid,
        floor_no=1,
        publisher="test_user",
        content="Hello world",
        pub_time="2025-01-01",
        has_images=False,
        image_urls=[],
    )
    snapshot = ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/thread-{tid}-1-1.html",
        page_type="thread",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="test_user",
        publisher_uid="12345",
        pub_time="2025-01-01",
        permission=0,
        floors=[floor],
        image_count=0,
    )
    ThreadsRepository(conn).upsert_snapshot(snapshot, context_path=f"threads/{tid}/context.md")


class TestEnsureThreadCacheHit:
    def test_cache_hit_returns_detail_payload_with_expected_keys(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        _seed_thread(db, tid=100)
        with patch("yamibo_mcp.application.legacy_use_cases.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.legacy_use_cases.connect", return_value=db):
            # Act
            result = ensure_thread(tid=100)
        # Assert
        assert _THREAD_DETAIL_EXTRA_KEYS.issubset(result.keys()), (
            f"Missing keys: {_THREAD_DETAIL_EXTRA_KEYS - result.keys()}"
        )
        assert result["tid"] == 100
        assert result["archive_status"] is not None

    def test_cache_hit_returns_stable_resources(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        _seed_thread(db, tid=200)
        with patch("yamibo_mcp.application.legacy_use_cases.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.legacy_use_cases.connect", return_value=db):
            # Act
            result = ensure_thread(tid=200)
        # Assert
        resources = result.get("resources", {})
        assert "context" in resources
        assert "metadata" in resources
        assert resources["context"].startswith("yamibo://threads/200/")


class TestEnsureThreadCacheMiss:
    def test_cache_miss_triggers_inline_sync_and_returns_detail(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)

        def fake_run_inline_sync(*, tid, url, base_url, forum_id, settings):
            _seed_thread(db, tid=tid)
            return {"job_id": "fake_job", "status": "succeeded", "artifacts": {}}

        with patch("yamibo_mcp.application.legacy_use_cases.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.legacy_use_cases.connect", return_value=db), \
             patch("yamibo_mcp.application.legacy_use_cases._run_inline_sync", side_effect=fake_run_inline_sync):
            # Act
            result = ensure_thread(tid=300)
        # Assert
        assert _THREAD_DETAIL_EXTRA_KEYS.issubset(result.keys())
        assert result["tid"] == 300

    def test_cache_miss_sync_failure_returns_error_payload(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        error_payload = {
            "tid": 400,
            "found": False,
            "archived": False,
            "message": "failed to sync thread 400 from remote",
            "sync_job": {"job_id": "j1", "status": "failed"},
            "error": {"code": "RemoteFetchError", "message": "connection refused"},
        }

        def fake_run_inline_sync(*, tid, url, base_url, forum_id, settings):
            return error_payload

        with patch("yamibo_mcp.application.legacy_use_cases.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.legacy_use_cases.connect", return_value=db), \
             patch("yamibo_mcp.application.legacy_use_cases._run_inline_sync", side_effect=fake_run_inline_sync):
            # Act
            result = ensure_thread(tid=400)
        # Assert
        assert result["found"] is False
        assert result["archived"] is False
        assert result["error"]["code"] == "RemoteFetchError"
        assert "cookie" not in str(result).lower()
        assert "password" not in str(result).lower()

    def test_cache_miss_sync_succeeds_but_thread_still_missing(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)

        def fake_run_inline_sync(*, tid, url, base_url, forum_id, settings):
            return {"job_id": "j2", "status": "succeeded", "artifacts": {}}

        with patch("yamibo_mcp.application.legacy_use_cases.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.legacy_use_cases.connect", return_value=db), \
             patch("yamibo_mcp.application.legacy_use_cases._run_inline_sync", side_effect=fake_run_inline_sync):
            # Act
            result = ensure_thread(tid=999)
        # Assert
        assert result["found"] is False
        assert "could not be loaded" in result["message"]


class TestArchiveThreadJob:
    def test_creates_queued_sync_thread_job_with_tid(self, tmp_path):
        # Arrange - use dedicated db to avoid fixture connection being closed
        import sqlite3 as _sqlite3
        from yamibo_mcp.db.migrations import migrate as _migrate
        db_path = tmp_path / "test.db"
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        _migrate(conn)
        settings = _fake_settings(tmp_path)
        with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.archive_commands.connect", return_value=conn):
            # Act
            result = archive_thread_job(tid=42)
        # Assert
        assert "job_id" in result
        assert isinstance(result["job_id"], str)
        assert len(result["job_id"]) > 0

    def test_preserves_tid_url_base_url_in_payload(self, tmp_path):
        # Arrange - use dedicated db to avoid fixture connection being closed
        import sqlite3 as _sqlite3
        from yamibo_mcp.db.migrations import migrate as _migrate
        db_path = tmp_path / "test.db"
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        _migrate(conn)
        settings = _fake_settings(tmp_path)
        with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.archive_commands.connect", return_value=conn):
            # Act
            result = archive_thread_job(tid=42, url="https://example.com/t/42", base_url="https://bbs.yamibo.com")
        # Assert - use a fresh connection since archive_thread_job closes the patched one
        verify_conn = _sqlite3.connect(str(db_path))
        verify_conn.row_factory = _sqlite3.Row
        try:
            from yamibo_mcp.db.repositories.jobs import JobsRepository
            job = JobsRepository(verify_conn).get(result["job_id"])
            assert job.tid == 42
            assert job.payload["tid"] == 42
            assert job.payload["url"] == "https://example.com/t/42"
            assert job.payload["base_url"] == "https://bbs.yamibo.com"
            assert job.job_type == "sync_thread"
            assert job.status == "queued"
        finally:
            verify_conn.close()

    def test_raises_on_missing_input(self, tmp_path):
        # Arrange
        settings = _fake_settings(tmp_path)
        # Act & Assert
        with pytest.raises(ValueError, match="archive_thread requires"):
            archive_thread_job()

    def test_preserves_html_path_in_payload(self, tmp_path):
        # Arrange - use dedicated db to avoid fixture connection being closed
        import sqlite3 as _sqlite3
        from yamibo_mcp.db.migrations import migrate as _migrate
        db_path = tmp_path / "test.db"
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        _migrate(conn)
        settings = _fake_settings(tmp_path)
        with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.archive_commands.connect", return_value=conn):
            # Act
            result = archive_thread_job(html_path="/tmp/test.html", tid=10)
        # Assert - use a fresh connection
        verify_conn = _sqlite3.connect(str(db_path))
        verify_conn.row_factory = _sqlite3.Row
        try:
            from yamibo_mcp.db.repositories.jobs import JobsRepository
            job = JobsRepository(verify_conn).get(result["job_id"])
            assert job.payload["html_path"] == "/tmp/test.html"
            assert job.payload["tid"] == 10
        finally:
            verify_conn.close()
