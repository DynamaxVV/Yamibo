from __future__ import annotations

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.server.agent_adapter import to_wire


def test_to_wire_always_returns_full_payload_shape_for_success():
    payload = to_wire(AgentResult(ok=True, data={"tid": 572313}))

    assert payload == {
        "ok": True,
        "data": {"tid": 572313},
        "error": None,
        "resources": {},
        "next_actions": [],
        "warnings": [],
        "side_effects": [],
    }


def test_to_wire_always_returns_full_payload_shape_for_failure():
    payload = to_wire(
        AgentResult(
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
    )

    assert payload == {
        "ok": False,
        "data": None,
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
        "resources": {},
        "next_actions": [],
        "warnings": [],
        "side_effects": [],
    }
