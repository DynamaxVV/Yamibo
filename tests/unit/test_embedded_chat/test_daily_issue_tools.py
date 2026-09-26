import asyncio
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from yamibo_mcp.application import daily_brief_service
from yamibo_mcp.services.embedded_chat.mcp import RestrictedTools


class Store:
    def __init__(self, status):
        self.operation = {"id": "op", "status": status}

    @contextmanager
    def transaction(self):
        yield self

    def get(self, kind, identifier, conn=None, **kwargs):
        return self.operation if kind == "operations" else {"parent_id": "trusted-session", "owner_id": "trusted-owner", "discussion_scope_id": "scope", "discussion_scope": {}}

    def guard(self, *args):
        return {"parent_id": "trusted-session"}

    def save(self, kind, value, conn):
        self.operation = value


def tools(status="approved"):
    tool = object.__new__(RestrictedTools)
    tool.store = Store(status)
    tool.policy = SimpleNamespace(run_id="run", request=lambda *args: tool.store.operation)
    tool.settings = SimpleNamespace(llm_api_key="", hermes_api_key="", db_url="", chat_access_token="", data_dir="/nonexistent")
    return tool


def test_daily_create_uses_trusted_session_and_replays_receipt(monkeypatch):
    tool = tools()
    calls = []

    def create(**kwargs):
        assert tool.store.operation["status"] == "outcome_unknown"
        calls.append(kwargs)
        return {"queued": True, "job_id": "job"}

    monkeypatch.setattr(daily_brief_service, "create_manual_issue", create)
    result = asyncio.run(tool.create_daily_issue("2023-08-28", [5, 5]))
    assert result["data"]["queued"] is True
    assert calls[0]["session_id"] == "trusted-session"
    assert calls[0]["target_day"] == date(2023, 8, 28)
    assert calls[0]["forum_ids"] == [5]
    assert asyncio.run(tool.create_daily_issue("2023-08-28", [5])) == result
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["denied", "outcome_unknown"])
def test_daily_create_does_not_execute_without_approval(monkeypatch, status):
    monkeypatch.setattr(daily_brief_service, "create_manual_issue", lambda **kw: pytest.fail("unexpected write"))
    assert not asyncio.run(tools(status).create_daily_issue("2023-08-28", [5]))["ok"]


def test_daily_read_preserves_owner_context(monkeypatch):
    captured = {}
    def get(settings, **kwargs):
        captured.update(kwargs)
        return {"issue": {"state": "queued"}}
    monkeypatch.setattr("yamibo_mcp.services.embedded_chat.mcp.daily_issue_status", get)
    assert tools().read_daily_issue("issue")["data"]["issue"]["state"] == "queued"
    assert captured["owner_id"] == "trusted-owner"
    assert captured["issue_id"] == "issue"


def test_daily_tool_schemas_do_not_accept_owner_or_session():
    from yamibo_mcp.config import load_settings
    from yamibo_mcp.services.embedded_chat.mcp import build_restricted_server

    server = build_restricted_server(load_settings(), "run")
    schemas = {tool.name: tool.inputSchema for tool in asyncio.run(server.list_tools())}
    assert set(schemas["create_daily_issue"]["properties"]) == {"target_day", "forum_ids"}
    assert set(schemas["read_daily_issue"]["properties"]) == {"issue_id"}
