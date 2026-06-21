from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from yamibo_mcp.yamibo.parsers.forum_list import ForumThreadItem


def _fake_settings(tmp_path: Path):
    settings = MagicMock()
    settings.db_path = str(tmp_path / "test.db")
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.cookie_file = tmp_path / "cookies.txt"
    settings.use_system_proxy = False
    settings.login_username = None
    settings.login_password = None
    return settings


def _make_fake_item(tid: int, title: str = "Test", *, sticky: bool = False) -> ForumThreadItem:
    return ForumThreadItem(
        tid=tid,
        title=title,
        url=f"https://bbs.yamibo.com/thread-{tid}-1-1.html",
        category=None,
        publisher="user",
        posted_at="2025-01-01",
        reply_count=0,
        row_kind="sticky" if sticky else "normal",
    )


def _make_fake_fetch_result(url: str):
    result = MagicMock()
    result.final_url = url
    result.html = "<html></html>"
    return result


class TestBrowseForumPageForumId:
    def test_default_forum_id_points_to_forum_30(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        fake_item = _make_fake_item(100)
        fake_result = _make_fake_fetch_result("https://bbs.yamibo.com/forum-30-1.html")

        mock_client = MagicMock()
        mock_client.fetch_forum_threads.return_value = (fake_result, [fake_item])

        from yamibo_mcp.application.remote_queries import browse_forum_page
        with patch("yamibo_mcp.application.remote_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.remote_queries.YamiboClient", return_value=mock_client), \
             patch("yamibo_mcp.application.remote_queries.connect", return_value=db):
            # Act
            result = browse_forum_page(page=1)
        # Assert
        _, kwargs = mock_client.fetch_forum_threads.call_args
        assert kwargs["forum_id"] == 30
        assert result["forum_id"] == 30

    def test_explicit_forum_id_55(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        fake_item = _make_fake_item(200)
        fake_result = _make_fake_fetch_result("https://bbs.yamibo.com/forum-55-1.html")

        mock_client = MagicMock()
        mock_client.fetch_forum_threads.return_value = (fake_result, [fake_item])

        from yamibo_mcp.application.remote_queries import browse_forum_page
        with patch("yamibo_mcp.application.remote_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.remote_queries.YamiboClient", return_value=mock_client), \
             patch("yamibo_mcp.application.remote_queries.connect", return_value=db):
            # Act
            result = browse_forum_page(page=1, forum_id=55)
        # Assert
        _, kwargs = mock_client.fetch_forum_threads.call_args
        assert kwargs["forum_id"] == 55
        assert result["forum_id"] == 55

    def test_explicit_forum_id_propagated_to_client(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        fake_result = _make_fake_fetch_result("https://bbs.yamibo.com/forum-5-1.html")

        mock_client = MagicMock()
        mock_client.fetch_forum_threads.return_value = (fake_result, [])

        from yamibo_mcp.application.remote_queries import browse_forum_page
        with patch("yamibo_mcp.application.remote_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.remote_queries.YamiboClient", return_value=mock_client), \
             patch("yamibo_mcp.application.remote_queries.connect", return_value=db):
            # Act
            browse_forum_page(page=1, forum_id=5)
        # Assert
        _, kwargs = mock_client.fetch_forum_threads.call_args
        assert kwargs["forum_id"] == 5


class TestSearchThreadsForumId:
    def test_default_forum_id_propagated_to_search(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)

        from yamibo_mcp.application.remote_queries import search_threads
        with patch("yamibo_mcp.application.remote_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.remote_queries._remote_search_items", return_value=([], [], 0)), \
             patch("yamibo_mcp.application.remote_queries.connect", return_value=db):
            # Act
            result = search_threads(query="test")
        # Assert
        assert result["forum_id"] == 30

    def test_explicit_forum_id_55_in_response(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)

        from yamibo_mcp.application.remote_queries import search_threads
        with patch("yamibo_mcp.application.remote_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.remote_queries._remote_search_items", return_value=([], [], 0)), \
             patch("yamibo_mcp.application.remote_queries.connect", return_value=db):
            # Act
            result = search_threads(query="test", forum_id=55)
        # Assert
        assert result["forum_id"] == 55

    def test_forum_id_passed_to_remote_search(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        mock_remote = MagicMock(return_value=([], [], 0))

        from yamibo_mcp.application.remote_queries import search_threads
        with patch("yamibo_mcp.application.remote_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.remote_queries._remote_search_items", mock_remote), \
             patch("yamibo_mcp.application.remote_queries.connect", return_value=db):
            # Act
            search_threads(query="test", forum_id=55)
        # Assert
        _, kwargs = mock_remote.call_args
        assert kwargs["forum_id"] == 55


class TestSyncForumRangeForumId:
    def test_default_forum_id_in_job_payload(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        fake_item = _make_fake_item(100)
        fake_result = _make_fake_fetch_result("https://bbs.yamibo.com/forum-30-1.html")

        mock_client = MagicMock()
        mock_client.fetch_forum_threads.return_value = (fake_result, [fake_item])

        from yamibo_mcp.server.legacy_tools import sync_forum_range
        with patch("yamibo_mcp.server.legacy_tools.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.legacy_tools.YamiboClient", return_value=mock_client), \
             patch("yamibo_mcp.server.legacy_tools.connect", return_value=db):
            # Act
            result = sync_forum_range(start_page=1, end_page=1)
        # Assert
        _, kwargs = mock_client.fetch_forum_threads.call_args
        assert kwargs["forum_id"] == 30

    def test_explicit_forum_id_in_job_payload(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        fake_item = _make_fake_item(200)
        fake_result = _make_fake_fetch_result("https://bbs.yamibo.com/forum-55-1.html")

        mock_client = MagicMock()
        mock_client.fetch_forum_threads.return_value = (fake_result, [fake_item])

        from yamibo_mcp.server.legacy_tools import sync_forum_range
        with patch("yamibo_mcp.server.legacy_tools.load_settings", return_value=settings), \
             patch("yamibo_mcp.server.legacy_tools.YamiboClient", return_value=mock_client), \
             patch("yamibo_mcp.server.legacy_tools.connect", return_value=db):
            # Act
            result = sync_forum_range(start_page=1, end_page=1, forum_id=55)
        # Assert
        _, kwargs = mock_client.fetch_forum_threads.call_args
        assert kwargs["forum_id"] == 55
        # Verify job payload carries forum_id using fresh connection
        import sqlite3 as _sqlite3
        verify_conn = _sqlite3.connect(settings.db_path)
        verify_conn.row_factory = _sqlite3.Row
        try:
            from yamibo_mcp.db.repositories.jobs import JobsRepository
            job = JobsRepository(verify_conn).get(result["items"][0]["job_id"])
            assert job.payload["forum_id"] == 55
        finally:
            verify_conn.close()


class TestArchiveThreadJobForumId:
    def test_forum_id_preserved_in_payload(self, tmp_path):
        # Arrange
        import sqlite3 as _sqlite3
        from yamibo_mcp.db.migrations import migrate as _migrate
        db_path = tmp_path / "test.db"
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        _migrate(conn)
        settings = _fake_settings(tmp_path)

        from yamibo_mcp.application.thread_use_cases import archive_thread_job
        with patch("yamibo_mcp.application.thread_use_cases.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.thread_use_cases.connect", return_value=conn):
            # Act
            result = archive_thread_job(tid=42, forum_id=55)
        # Assert
        verify_conn = _sqlite3.connect(str(db_path))
        verify_conn.row_factory = _sqlite3.Row
        try:
            from yamibo_mcp.db.repositories.jobs import JobsRepository
            job = JobsRepository(verify_conn).get(result["job_id"])
            assert job.payload["forum_id"] == 55
        finally:
            verify_conn.close()

    def test_omitted_forum_id_not_in_payload(self, tmp_path):
        # Arrange
        import sqlite3 as _sqlite3
        from yamibo_mcp.db.migrations import migrate as _migrate
        db_path = tmp_path / "test.db"
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        _migrate(conn)
        settings = _fake_settings(tmp_path)

        from yamibo_mcp.application.thread_use_cases import archive_thread_job
        with patch("yamibo_mcp.application.thread_use_cases.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.thread_use_cases.connect", return_value=conn):
            # Act
            result = archive_thread_job(tid=42)
        # Assert
        verify_conn = _sqlite3.connect(str(db_path))
        verify_conn.row_factory = _sqlite3.Row
        try:
            from yamibo_mcp.db.repositories.jobs import JobsRepository
            job = JobsRepository(verify_conn).get(result["job_id"])
            assert "forum_id" not in job.payload
        finally:
            verify_conn.close()
