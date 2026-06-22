from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentAction:
    tool: str
    args: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class AgentError:
    code: str
    message: str
    agent_hint: str
    retryable: bool = False
    field_errors: list[dict[str, Any]] = field(default_factory=list)
    suggested_actions: list[AgentAction] = field(default_factory=list)


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: AgentError | None = None
    resources: dict[str, str] = field(default_factory=dict)
    next_actions: list[AgentAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)


AgentResponse = AgentResult


def success(
    data: dict[str, Any],
    *,
    resources: dict[str, str] | None = None,
    next_actions: list[str] | list[AgentAction] | None = None,
    warnings: list[str] | None = None,
    side_effects: list[str] | None = None,
) -> dict[str, Any]:
    actions = _normalize_actions(next_actions)
    return _to_dict(
        AgentResult(
            ok=True,
            data=data,
            resources=resources or {},
            next_actions=actions,
            warnings=warnings or [],
            side_effects=side_effects or [],
        )
    )


def failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    suggested_action: str | None = None,
    agent_hint: str | None = None,
) -> dict[str, Any]:
    suggested_actions = []
    if suggested_action:
        suggested_actions.append(
            AgentAction(tool="manual_follow_up", args={}, reason=suggested_action)
        )
    return _to_dict(
        AgentResult(
            ok=False,
            error=AgentError(
                code=code,
                message=message,
                agent_hint=agent_hint or message,
                retryable=retryable,
                suggested_actions=suggested_actions,
            ),
        )
    )


def _normalize_actions(
    actions: list[str] | list[AgentAction] | None,
) -> list[AgentAction]:
    if not actions:
        return []
    normalized: list[AgentAction] = []
    for action in actions:
        if isinstance(action, AgentAction):
            normalized.append(action)
        else:
            normalized.append(AgentAction(tool=action, args={}, reason=action))
    return normalized


def _to_dict(resp: AgentResult) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": resp.ok}
    if resp.data is not None:
        payload["data"] = resp.data
    if resp.error is not None:
        payload["error"] = _error_to_dict(resp.error)
    if resp.resources:
        payload["resources"] = resp.resources
    if resp.next_actions:
        payload["next_actions"] = [_action_to_dict(action) for action in resp.next_actions]
    if resp.warnings:
        payload["warnings"] = resp.warnings
    if resp.side_effects:
        payload["side_effects"] = resp.side_effects
    return payload


def _error_to_dict(error: AgentError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
        "agent_hint": error.agent_hint,
    }
    if error.retryable:
        payload["retryable"] = True
    if error.field_errors:
        payload["field_errors"] = error.field_errors
    if error.suggested_actions:
        payload["suggested_actions"] = [
            _action_to_dict(action) for action in error.suggested_actions
        ]
    elif not error.retryable:
        payload["retryable"] = False
    return payload


def _action_to_dict(action: AgentAction) -> dict[str, Any]:
    return {
        "tool": action.tool,
        "args": action.args,
        "reason": action.reason,
    }
