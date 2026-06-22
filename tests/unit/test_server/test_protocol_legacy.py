from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.server.legacy_protocol import handle_request, list_tools_payload


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
    return settings


class TestLegacyJsonRpcDispatch:
    def test_initialize_returns_server_info(self):
        # Arrange
        request = {"id": 1, "method": "initialize", "params": {}}
        # Act
        response = handle_request(request)
        # Assert
        assert response["id"] == 1
        assert response["result"]["server"] == "yamibo-mcp"

    def test_ping_returns_ok(self):
        # Arrange
        request = {"id": 2, "method": "ping", "params": {}}
        # Act
        response = handle_request(request)
        # Assert
        assert response["result"]["ok"] is True

    def test_tools_list_includes_all_expected_tools(self):
        # Arrange
        request = {"id": 3, "method": "tools/list", "params": {}}
        # Act
        response = handle_request(request)
        # Assert
        tool_names = {t["name"] for t in response["result"]["tools"]}
        expected = {
            "browse_forum_page",
            "search_forum_threads",
            "inspect_remote_thread",
            "create_thread_archive_job",
            "ensure_thread_archived",
            "read_archived_thread",
            "check_thread_updates",
            "create_thread_update_job",
            "create_thread_export_job",
            "read_job",
            "read_job_events",
            "read_forum_profiles",
        }
        assert expected.issubset(tool_names)

    def test_tools_call_create_thread_update_job_returns_job_id(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        request = {
            "id": 4,
            "method": "tools/call",
            "params": {"name": "create_thread_update_job", "arguments": {"tid": 42}},
        }
        with patch("yamibo_mcp.application.update_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.update_commands.connect", return_value=db):
            response = handle_request(request)
        result = response["result"]
        assert result["ok"] is True
        assert result["data"]["job_id"].startswith("update_thread_")

    def test_tools_call_create_thread_archive_job_returns_job_id(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        request = {
            "id": 4,
            "method": "tools/call",
            "params": {"name": "create_thread_archive_job", "arguments": {"tid": 42}},
        }
        with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.archive_commands.connect", return_value=db):
            # Act
            response = handle_request(request)
        # Assert
        result = response["result"]
        assert result["ok"] is True
        assert "job_id" in result["data"]

    def test_tools_call_read_job_returns_status(self, tmp_path, db):
        # Arrange
        settings = _fake_settings(tmp_path)
        repo = JobsRepository(db)
        job = repo.create("noop")
        request = {
            "id": 5,
            "method": "tools/call",
            "params": {"name": "read_job", "arguments": {"job_id": job.job_id}},
        }
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db):
            # Act
            response = handle_request(request)
        # Assert
        result = response["result"]
        assert result["ok"] is True
        assert result["data"]["job_id"] == job.job_id
        assert result["data"]["status"] == "queued"

    def test_tools_call_unknown_tool_returns_error(self):
        # Arrange
        request = {
            "id": 6,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
        # Act
        response = handle_request(request)
        # Assert
        assert "error" in response
        assert "unknown tool" in response["error"]["message"]

    def test_unsupported_method_returns_error(self):
        # Arrange
        request = {"id": 7, "method": "unknown/method", "params": {}}
        # Act
        response = handle_request(request)
        # Assert
        assert "error" in response
        assert "unsupported method" in response["error"]["message"]


class TestLegacyProtocolSecurity:
    def test_error_response_does_not_leak_secrets(self, tmp_path, db):
        """Error responses should contain structured code/message, not raw internals."""
        # Arrange
        settings = _fake_settings(tmp_path)
        request = {
            "id": 8,
            "method": "tools/call",
            "params": {"name": "read_job", "arguments": {"job_id": "nonexistent"}},
        }
        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db):
            # Act
            response = handle_request(request)
        # Assert
        assert response["result"]["ok"] is False
        error = response["result"]["error"]
        assert error["code"] == "JOB_NOT_FOUND"
        assert "agent_hint" in error
        error_text = str(error).lower()
        assert "cookie" not in error_text
        assert "password" not in error_text
        assert "api_key" not in error_text


class TestListToolsPayload:
    def test_returns_all_tool_names(self):
        # Act
        payload = list_tools_payload()
        # Assert
        names = {t["name"] for t in payload["tools"]}
        assert "ensure_thread_archived" in names
        assert "create_thread_archive_job" in names
        assert "read_job" in names
        assert "search_forum_threads" in names
        assert "browse_forum_page" in names
