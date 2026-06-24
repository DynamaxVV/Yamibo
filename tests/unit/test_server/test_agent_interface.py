from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.server.agent_adapter import to_wire
from yamibo_mcp.server.agent_tools import (
    PUBLIC_AGENT_TOOLS,
    create_rag_index_batch_jobs,
    create_thread_archive_batch_jobs,
    create_thread_archive_job,
    inspect_remote_thread,
    probe_archived_threads,
    read_archived_thread,
    read_job,
    wait_for_job,
    search_forum_threads,
)
from yamibo_mcp.server.mcp_registry import register_agent_tools


RECOMMENDED_AGENT_TOOLS = {
    "browse_forum_page",
    "search_forum_threads",
    "inspect_remote_thread",
    "create_thread_archive_job",
    "create_thread_archive_batch_jobs",
    "ensure_thread_archived",
    "read_archived_thread",
    "probe_archived_threads",
    "check_thread_updates",
    "create_thread_update_job",
    "create_thread_export_job",
    "create_rag_index_job",
    "create_rag_index_batch_jobs",
    "search_archived_content",
    "read_job",
    "read_job_events",
    "wait_for_job",
    "read_forum_profiles",
}


def _fake_settings(tmp_path: Path):
    settings = MagicMock()
    settings.db_path = str(tmp_path / "test.db")
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.novel_txt_export_dir = tmp_path / "novel_exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.novel_txt_export_dir.mkdir(parents=True, exist_ok=True)
    settings.cookie_file = tmp_path / "cookies.txt"
    settings.use_system_proxy = False
    settings.login_username = None
    settings.login_password = None
    settings.request_interval_seconds = 0.0
    settings.request_interval_jitter_seconds = 0.0
    return settings


class TestPublicAgentTools:
    def test_public_tools_exclude_internal_llm_helpers(self):
        names = {name for name, _, _ in PUBLIC_AGENT_TOOLS}

        assert "llm_transform_text" not in names
        assert "parse_thread_title" not in names

    def test_public_tools_only_include_recommended_agent_facing_names(self):
        names = {name for name, _, _ in PUBLIC_AGENT_TOOLS}

        assert names == RECOMMENDED_AGENT_TOOLS

    def test_public_tool_handlers_are_agent_enveloped(self):
        missing = [
            name
            for name, _, handler in PUBLIC_AGENT_TOOLS
            if not getattr(handler, "__agent_tool__", False)
        ]

        assert missing == []

    def test_public_search_tool_signature_does_not_expose_limit(self):
        signature = inspect.signature(search_forum_threads)

        assert "limit" not in signature.parameters

    def test_read_archived_thread_signature_exposes_view_and_floor_range(self):
        signature = inspect.signature(read_archived_thread)

        assert "view" in signature.parameters
        assert "floor_start" in signature.parameters
        assert "floor_end" in signature.parameters
        assert "cursor" in signature.parameters
        assert "chunk_size" in signature.parameters

    def test_probe_archived_threads_signature_exposes_tids(self):
        signature = inspect.signature(probe_archived_threads)

        assert list(signature.parameters) == ["tids"]

    def test_public_tools_list_does_not_expose_limit_parameter(self):
        for name, _, handler in PUBLIC_AGENT_TOOLS:
            assert "limit" not in inspect.signature(handler).parameters, name

    def test_mcp_registry_only_registers_recommended_agent_tools(self):
        class FakeServer:
            def __init__(self):
                self.names: list[str] = []

            def tool(self, *, name: str, description: str):
                self.names.append(name)

                def decorator(fn):
                    return fn

                return decorator

        fake = FakeServer()

        register_agent_tools(fake)

        assert set(fake.names) == RECOMMENDED_AGENT_TOOLS


class TestStructuredAgentWireFormat:
    def test_to_wire_omits_empty_optional_fields(self):
        result = AgentResult(ok=True, data={"tid": 1})

        assert to_wire(result) == {"ok": True, "data": {"tid": 1}}

    def test_to_wire_serializes_actions_and_errors(self):
        result = AgentResult(
            ok=False,
            error=AgentError(
                code="LOCAL_ARCHIVE_NOT_FOUND",
                message="Thread 572313 is not archived locally.",
                agent_hint="Create an archive job before reading local content.",
                suggested_actions=[
                    AgentAction(
                        tool="create_thread_archive_job",
                        args={"tid": 572313},
                        reason="Archive the thread locally before reading content.",
                    )
                ],
            ),
        )

        assert to_wire(result) == {
            "ok": False,
            "error": {
                "code": "LOCAL_ARCHIVE_NOT_FOUND",
                "message": "Thread 572313 is not archived locally.",
                "agent_hint": "Create an archive job before reading local content.",
                "retryable": False,
                "suggested_actions": [
                    {
                        "tool": "create_thread_archive_job",
                        "args": {"tid": 572313},
                        "reason": "Archive the thread locally before reading content.",
                    }
                ],
            },
        }


class TestLocalVsRemoteIsolation:
    def test_read_archived_thread_does_not_fetch_remote_when_local_missing(self, tmp_path, db):
        settings = _fake_settings(tmp_path)

        import yamibo_mcp.application.archive_queries as archive_queries

        with patch("yamibo_mcp.application.archive_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.archive_queries.connect", return_value=db):
            result = read_archived_thread(tid=572313, view="summary")

        assert result["ok"] is False
        assert result["error"]["code"] == "LOCAL_ARCHIVE_NOT_FOUND"
        assert not hasattr(archive_queries, "YamiboClient")

    def test_inspect_remote_thread_does_not_write_sqlite(self, tmp_path):
        settings = _fake_settings(tmp_path)
        fake_fetch = MagicMock()
        fake_fetch.final_url = "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=572313"
        fake_fetch.html = "<html></html>"
        fake_snapshot = MagicMock()
        fake_snapshot.tid = 572313
        fake_snapshot.display_title = "Remote Title"
        fake_snapshot.publisher = "publisher"
        fake_snapshot.publisher_uid = "42"
        fake_snapshot.floors = [MagicMock(content="floor 1", floor_no=1), MagicMock(content="floor 2", floor_no=2)]
        fake_snapshot.image_count = 3

        with patch("yamibo_mcp.application.remote_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.remote_queries.YamiboClient") as mock_client_cls, \
             patch("yamibo_mcp.application.remote_queries.parse_thread_snapshot", return_value=fake_snapshot), \
             patch("yamibo_mcp.application.remote_queries.extract_forum_id_from_html", return_value=30), \
             patch("yamibo_mcp.application.remote_queries.extract_category_from_html", return_value="漫画区"):
            mock_client_cls.return_value.fetch_thread.return_value = fake_fetch
            result = inspect_remote_thread(tid=572313)

        assert result["ok"] is True
        assert result["data"]["tid"] == 572313
        assert result["side_effects"] == ["remote_fetch_only"]


class TestAgentStatusHints:
    def test_create_thread_archive_job_reuses_live_job_and_reports_no_new_write(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        from yamibo_mcp.db.repositories.jobs import JobsRepository

        existing = JobsRepository(db).create("sync_thread", tid=572313, payload={"tid": 572313})

        with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.archive_commands.connect", return_value=db):
            result = create_thread_archive_job(tid=572313)

        assert result["ok"] is True
        assert result["data"]["job_id"] == existing.job_id
        assert result["data"]["created"] is False
        assert "sqlite_job_reused" in result["side_effects"]
        assert "sqlite_job_created" not in result["side_effects"]

    def test_create_thread_archive_batch_jobs_returns_created_and_reused_counts(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        from yamibo_mcp.db.repositories.jobs import JobsRepository

        existing = JobsRepository(db).create("sync_thread", tid=572313, payload={"tid": 572313})

        with patch("yamibo_mcp.application.archive_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.archive_commands.connect", return_value=db):
            result = create_thread_archive_batch_jobs(tids=[572313, 572314])

        assert result["ok"] is True
        assert result["data"]["target_count"] == 2
        assert result["data"]["created_count"] == 1
        assert result["data"]["reused_count"] == 1
        assert existing.job_id in result["data"]["reused_job_ids"]

    def test_create_rag_index_batch_jobs_returns_created_and_reused_counts(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        from yamibo_mcp.db.repositories.jobs import JobsRepository

        existing = JobsRepository(db).create("rag_index", tid=7001, payload={"force": False, "embedding_dimensions": 512})
        settings.rag_embedding_dimensions = 512

        with patch("yamibo_mcp.application.rag_commands.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.rag_commands.connect", return_value=db):
            result = create_rag_index_batch_jobs(tids=[7001, 7002])

        assert result["ok"] is True
        assert result["data"]["target_count"] == 2
        assert result["data"]["created_count"] == 1
        assert result["data"]["reused_count"] == 1
        assert existing.job_id in result["data"]["reused_job_ids"]

    def test_read_job_exposes_terminality_fields(self, tmp_path, db):
        settings = _fake_settings(tmp_path)
        from yamibo_mcp.db.repositories.jobs import JobsRepository

        job = JobsRepository(db).create("noop")

        with patch("yamibo_mcp.application.job_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.job_queries.connect", return_value=db):
            result = read_job(job_id=job.job_id)

        assert result["ok"] is True
        assert result["data"]["is_terminal"] is False
        assert result["data"]["result_ready"] is False
        assert result["data"]["recommended_poll_after_seconds"] == 2
