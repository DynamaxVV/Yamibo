import asyncio

import pytest

from yamibo_mcp.config import load_settings
from yamibo_mcp.services.embedded_chat import mcp


@pytest.fixture
def server(monkeypatch):
    async def invoke(self, name, args, handler, **kwargs):
        return await handler()
    monkeypatch.setattr(mcp.RestrictedTools, "invoke", invoke)
    return mcp.build_restricted_server(load_settings(), "test-run")


def call(server, exposed_name, **args):
    # FastMCP executes its generated schema validation as in a protocol call.
    return asyncio.run(server._tool_manager.call_tool(exposed_name, args))


def test_directory_covers_every_public_capability_without_eager_schemas(server):
    exposed = {t.name for t in asyncio.run(server.list_tools())}
    assert not exposed.intersection(mcp.PUBLIC_TOOLS)
    found = call(server, "discover_public_tools")
    assert {t["name"] for t in found["tools"]} == set(mcp.PUBLIC_TOOLS)
    assert all("input_schema" not in t for t in found["tools"])
    result = call(server, "discover_public_tools", query="search_forum_threads")
    assert [t["name"] for t in result["tools"]] == ["search_forum_threads"]


def test_describe_has_safe_schema_and_guidance(server):
    for name in mcp.PUBLIC_TOOLS:
        result = call(server, "describe_public_tool", name=name)
        assert not set(result["input_schema"].get("properties", {})) & mcp.UNSAFE_PUBLIC_ARGUMENTS
        assert "Capability metadata:" in result["description"]
    assert not call(server, "describe_public_tool", name="shell")["ok"]


def test_execution_delegates_exactly_to_existing_policy_bridge(server, monkeypatch):
    seen = []
    async def public_call(self, name, args):
        seen.append((name, args))
        return {"ok": True}
    monkeypatch.setattr(mcp.RestrictedTools, "public_call", public_call)
    args = {"query": "星灵感应", "forum_id": 30}
    assert call(server, "call_public_tool", tool_name="search_forum_threads", arguments=args)["ok"]
    assert seen == [("search_forum_threads", args)]
    assert not call(server, "call_public_tool", tool_name="shell", arguments={})["ok"]
    assert len(seen) == 1


def test_approved_skill_revision_is_used_by_later_tool_reads(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from yamibo_mcp.services.embedded_chat.skill_proposals import SkillProposals

    async def invoke(self, name, args, handler, **kwargs):
        return await handler()

    monkeypatch.setattr(mcp.RestrictedTools, "invoke", invoke)
    settings = SimpleNamespace(data_dir=tmp_path)
    server = mcp.build_restricted_server(settings, "test-run")
    initial = call(server, "read_project_skill", name="forum-search")
    revision = initial["data"]["revision"]
    proposed = call(server, "propose_project_skill_update", name="forum-search",
                    content="Revised forum guidance", reason="Correct outdated steps",
                    expected_revision=revision)
    assert proposed["ok"]
    assert call(server, "read_project_skill", name="forum-search")["data"]["revision"] == revision
    SkillProposals(settings).review(proposed["data"]["id"], approve=True)
    updated = call(server, "read_project_skill", name="forum-search")
    assert updated["data"]["content"] == "Revised forum guidance"
    assert updated["data"]["revision"] != revision
