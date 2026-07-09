from __future__ import annotations

import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from yamibo_mcp.web.routes.chat import handle_chat_context, handle_chat_turn


class _CaptureHandler:
    def __init__(self, *, method: str = "GET", body: dict | None = None):
        self.command = method
        self.headers = {}
        self.status = None
        self.sent_headers: list[tuple[str, str]] = []
        raw = json.dumps(body or {}).encode("utf-8")
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers["Content-Length"] = str(len(raw))

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass


def _settings(tmp_path, *, transport: str = "cli", sse_url: str | None = None):
    return SimpleNamespace(
        project_root=tmp_path,
        llm_model="gpt-test",
        llm_api_key="sk-test",
        llm_base_url="https://example.invalid/v1",
        chat_transport=transport,
        chat_mcp_sse_url=sse_url,
    )


def test_chat_context_generates_project_runtime_files(tmp_path):
    settings = _settings(tmp_path)
    handler = _CaptureHandler()

    handle_chat_context(handler, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["transport"] == "cli"
    assert "prompts" not in payload
    assert "skills" not in payload
    assert (tmp_path / ".yamibo" / "chat" / "AGENT.md").exists()
    assert (tmp_path / ".yamibo" / "chat" / "SKILL.md").exists()
    assert (tmp_path / ".yamibo" / "chat" / "PROMPT.md").exists()


def test_chat_turn_executes_cli_plan(tmp_path):
    settings = _settings(tmp_path)
    handler = _CaptureHandler(
        method="POST",
        body={
            "message": "帮我搜索星灵感应",
            "history": [{"role": "user", "content": "先看漫画区"}],
        },
    )

    with (
        patch(
            "yamibo_mcp.services.web_chat.openai_compatible_chat",
            return_value={
                "model": "gpt-test",
                "content": json.dumps(
                    {
                        "assistant_message": "我会先搜索远端帖子。",
                        "commands": [
                            {
                                "command": "search-threads",
                                "args": {"query": "星灵感应", "forum_id": 30},
                                "reason": "先拿到候选 tid。",
                            }
                        ],
                        "warnings": [],
                    },
                    ensure_ascii=False,
                ),
            },
        ),
        patch(
            "yamibo_mcp.services.web_chat.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["uv", "run", "yamibo-archiver", "search-threads"],
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"items": [{"tid": 572313}]}}),
                stderr="",
            ),
        ) as run_mock,
    ):
        handle_chat_turn(handler, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["assistant_message"] == "我会先搜索远端帖子。"
    assert payload["transport"] == "cli"
    assert payload["commands"][0]["executed"] is True
    assert payload["commands"][0]["ok"] is True
    assert payload["commands"][0]["output"]["data"]["items"][0]["tid"] == 572313
    run_mock.assert_called_once()


def test_chat_turn_uses_mcp_stdio_transport(tmp_path):
    settings = _settings(tmp_path, transport="mcp_stdio")
    handler = _CaptureHandler(method="POST", body={"message": "读取 job 状态"})

    with (
        patch(
            "yamibo_mcp.services.web_chat.openai_compatible_chat",
            return_value={
                "model": "gpt-test",
                "content": json.dumps(
                    {
                        "assistant_message": "我会读取 job 状态。",
                        "commands": [{"command": "job-status", "args": {"job_id": "job_1"}, "reason": "确认结果。"}],
                        "warnings": [],
                    },
                    ensure_ascii=False,
                ),
            },
        ),
        patch(
            "yamibo_mcp.services.web_chat._execute_mcp_action_stdio",
            return_value={
                "executed": True,
                "ok": True,
                "invocation": "tool:read_job",
                "output": {"job_id": "job_1", "status": "done"},
            },
        ),
    ):
        handle_chat_turn(handler, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["transport"] == "mcp_stdio"
    assert payload["commands"][0]["invocation"] == "tool:read_job"
    assert payload["commands"][0]["output"]["status"] == "done"


def test_chat_turn_requires_sse_url_for_mcp_sse(tmp_path):
    settings = _settings(tmp_path, transport="mcp_sse", sse_url=None)
    handler = _CaptureHandler(method="POST", body={"message": "读取 job 状态"})

    with patch(
        "yamibo_mcp.services.web_chat.openai_compatible_chat",
        return_value={
            "model": "gpt-test",
            "content": json.dumps(
                {
                    "assistant_message": "我会读取 job 状态。",
                    "commands": [{"command": "job-status", "args": {"job_id": "job_1"}, "reason": "确认结果。"}],
                    "warnings": [],
                },
                ensure_ascii=False,
            ),
        },
    ):
        handle_chat_turn(handler, settings)

    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert payload["transport"] == "mcp_sse"
    assert payload["commands"][0]["executed"] is False
    assert payload["commands"][0]["warning"] == "MCP SSE URL is not configured"
