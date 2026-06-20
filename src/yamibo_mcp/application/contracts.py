from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentResponse:
    ok: bool
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    resources: dict[str, str] = field(default_factory=dict)
    next_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def success(
    data: dict[str, Any],
    *,
    resources: dict[str, str] | None = None,
    next_actions: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    resp = AgentResponse(
        ok=True,
        data=data,
        resources=resources or {},
        next_actions=next_actions or [],
        warnings=warnings or [],
    )
    return _to_dict(resp)


def failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    suggested_action: str | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if retryable:
        error["retryable"] = True
    if suggested_action:
        error["suggested_action"] = suggested_action
    resp = AgentResponse(ok=False, error=error)
    return _to_dict(resp)


def _to_dict(resp: AgentResponse) -> dict[str, Any]:
    d: dict[str, Any] = {"ok": resp.ok}
    if resp.data is not None:
        d["data"] = resp.data
    if resp.error is not None:
        d["error"] = resp.error
    if resp.resources:
        d["resources"] = resp.resources
    if resp.next_actions:
        d["next_actions"] = resp.next_actions
    if resp.warnings:
        d["warnings"] = resp.warnings
    return d
