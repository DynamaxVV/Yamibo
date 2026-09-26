import asyncio

import pytest

from yamibo_mcp.services.embedded_chat.project_skills import list_skills, read_skill, soul
from yamibo_mcp.services.embedded_chat.mcp import build_restricted_server
from yamibo_mcp.config import load_settings


def test_project_soul_and_all_skills_are_packaged():
    assert "Yamibo" in soul()
    for item in list_skills():
        result = read_skill(item["name"])
        assert result["content"].startswith("# ")
        assert result["next_offset"] is None
    with pytest.raises(ValueError, match="INVALID_PROJECT_SKILL"):
        read_skill("../../secrets")


def test_project_skill_tools_are_registered_without_database_read():
    server = build_restricted_server(load_settings(), "test-run")
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {"list_project_skills", "read_project_skill", "propose_agent_memory",
            "confirm_agent_memory", "cancel_agent_memory"} <= names
