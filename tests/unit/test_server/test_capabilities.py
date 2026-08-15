from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.server.agent_adapter import agent_tool, capability_registration
from yamibo_mcp.server.agent_tools import PUBLIC_AGENT_TOOLS
from yamibo_mcp.server.capabilities import EFFECTS, RISK_LEVELS, build_capability_manifest
from yamibo_mcp.server.resources import read_resource


def test_capability_manifest_covers_public_tools_once_and_matches_signatures():
    manifest = build_capability_manifest(PUBLIC_AGENT_TOOLS)
    capabilities = manifest["capabilities"]
    registered_names = [name for name, _, _ in PUBLIC_AGENT_TOOLS]

    assert [item["name"] for item in capabilities] == registered_names
    assert [item["description"] for item in capabilities] == [
        description for _, description, _ in PUBLIC_AGENT_TOOLS
    ]
    assert len(registered_names) == len(set(registered_names))
    assert set(manifest["effects"]) == EFFECTS
    assert set(manifest["risk_levels"]) == RISK_LEVELS
    assert {item["effect"] for item in capabilities} == {
        "read_only",
        "remote_read",
        "enqueue_job",
    }
    for item in capabilities:
        required = {
            "version",
            "input_schema",
            "output_schema",
            "effect",
            "risk",
            "idempotency",
            "requires",
            "cost_hints",
            "produces",
            "followups",
            "timeout_class",
        }
        assert required.issubset(item)
        if item["effect"] == "enqueue_job":
            assert item["terminal_read"]["tool"] == "read_job"
            assert item["terminal_read"]["resource"] == "yamibo://jobs/{job_id}/status"
            assert item["terminal_read"]["job_id_paths"]
    for name, _, handler in PUBLIC_AGENT_TOOLS:
        capability = next(item for item in capabilities if item["name"] == name)
        assert set(capability["input_schema"]["properties"]) == set(inspect.signature(handler).parameters)


def test_capabilities_resource_is_machine_readable_and_tools_schema_remains_available():
    capabilities = json.loads(read_resource("yamibo://schema/capabilities")["text"])
    tools = json.loads(read_resource("yamibo://schema/tools")["text"])

    assert capabilities["schema_version"] == "1"
    assert {item["name"] for item in capabilities["capabilities"]} == {item["name"] for item in tools["tools"]}
    assert "profiles" not in capabilities
    assert "citation_contract" not in capabilities
    assert "artifact_contract" not in capabilities
    archive_job = next(
        item
        for item in capabilities["capabilities"]
        if item["name"] == "create_thread_archive_job"
    )
    trend_job = next(
        item
        for item in capabilities["capabilities"]
        if item["name"] == "create_discussion_trend_index_job"
    )
    assert archive_job["input_schema"]["anyOf"] == [
        {"required": ["tid"]},
        {"required": ["url"]},
        {"required": ["html_path"]},
    ]
    assert trend_job["input_schema"]["properties"]["thresholds"]["type"] == ["object", "null"]


def test_public_capability_requires_registered_metadata():
    @agent_tool
    def undocumented_tool() -> AgentResult:
        return AgentResult(ok=True)

    with pytest.raises(ValueError, match="has no registered metadata"):
        build_capability_manifest([("undocumented_tool", "Missing metadata.", undocumented_tool)])


def test_manifest_rejects_unknown_followup():
    @agent_tool
    def isolated_tool() -> AgentResult:
        return AgentResult(ok=True)

    registration = capability_registration(
        "isolated_tool",
        "Read-only test tool.",
        isolated_tool,
        version="1",
        effect="read_only",
        risk="read",
        idempotency={"mode": "safe_to_retry", "scope": []},
        requires=["database"],
        cost_hints={"remote_requests": 0},
        produces=["agent_result"],
        followups=["missing_tool"],
        timeout_class="short",
    )

    with pytest.raises(ValueError, match="unknown followups"):
        build_capability_manifest([registration])


def test_agent_interface_tool_table_matches_public_registration():
    doc_path = Path(__file__).parents[3] / "docs" / "智能体接口说明.md"
    documented = set(
        re.findall(
            r"^\| `([^`]+)` \| `uv run yamibo-archiver",
            doc_path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )

    assert documented == {name for name, _, _ in PUBLIC_AGENT_TOOLS}
