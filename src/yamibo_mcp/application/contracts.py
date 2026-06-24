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
