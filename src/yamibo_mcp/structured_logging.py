from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
from typing import Any

from yamibo_mcp.context import get_log_context

SERVICE_NAME = "yamibo_mcp"


def emit(
    logger: logging.Logger,
    level: int,
    event_type: str,
    message: str,
    *,
    result: str = "success",
    status: str = "ok",
    error_code: str | None = None,
    error_message: str | None = None,
    retryable: bool | None = None,
    attempt: int | None = None,
    duration_ms: float | None = None,
    fallback_mode: str | None = None,
    fallback_reason: str | None = None,
    warning_codes: list[str] | None = None,
    agent_hint: str | None = None,
    next_action: str | None = None,
    resource_uri: str | None = None,
    payload: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    data_version: str | None = None,
    operation: str | None = None,
    **extra: Any,
) -> None:
    """Emit a structured log event with the given fields set as LogRecord attrs.

    Context fields (job_id, tid, etc.) are auto-injected from contextvars by
    the formatter unless explicitly overridden via ``**extra``.
    """
    extra = dict(extra)
    extra["event_type"] = event_type
    extra["result"] = result
    extra["status"] = status
    if error_code is not None:
        extra["error_code"] = error_code
    if error_message is not None:
        extra["error_message"] = error_message
    if retryable is not None:
        extra["retryable"] = retryable
    if attempt is not None:
        extra["attempt"] = attempt
    if duration_ms is not None:
        extra["duration_ms"] = duration_ms
    if fallback_mode is not None:
        extra["fallback_mode"] = fallback_mode
    if fallback_reason is not None:
        extra["fallback_reason"] = fallback_reason
    if warning_codes is not None:
        extra["warning_codes"] = warning_codes
    if agent_hint is not None:
        extra["agent_hint"] = agent_hint
    if next_action is not None:
        extra["next_action"] = next_action
    if resource_uri is not None:
        extra["resource_uri"] = resource_uri
    if payload is not None:
        extra["payload"] = payload
    if context is not None:
        extra["context"] = context
    if tags is not None:
        extra["tags"] = tags
    if data_version is not None:
        extra["data_version"] = data_version
    if operation is not None:
        extra["operation"] = operation
    logger.log(level, message, extra=extra)


@dataclass(slots=True)
class StructuredLogEvent:
    ts: str
    level: str
    service: str
    component: str
    event_type: str
    message: str
    result: str
    status: str
    job_id: str | None = None
    job_type: str | None = None
    tid: int | None = None
    forum_id: int | None = None
    run_id: str | None = None
    stage: str | None = None
    worker_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool | None = None
    attempt: int | None = None
    duration_ms: float | None = None
    fallback_mode: str | None = None
    fallback_reason: str | None = None
    warning_codes: list[str] = field(default_factory=list)
    agent_hint: str | None = None
    next_action: str | None = None
    resource_uri: str | None = None
    trace_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    data_version: str | None = None
    operation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "ts": self.ts,
            "level": self.level,
            "service": self.service,
            "component": self.component,
            "event_type": self.event_type,
            "message": self.message,
            "result": self.result,
            "status": self.status,
            "job_id": self.job_id,
            "job_type": self.job_type,
            "tid": self.tid,
            "forum_id": self.forum_id,
            "run_id": self.run_id,
            "stage": self.stage,
            "worker_id": self.worker_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retryable": self.retryable,
            "attempt": self.attempt,
            "duration_ms": self.duration_ms,
            "fallback_mode": self.fallback_mode,
            "fallback_reason": self.fallback_reason,
            "warning_codes": self.warning_codes,
            "agent_hint": self.agent_hint,
            "next_action": self.next_action,
            "resource_uri": self.resource_uri,
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "context": self.context,
            "tags": self.tags,
            "data_version": self.data_version,
            "operation": self.operation,
        }
        return {key: value for key, value in data.items() if value is not None}


class StructuredJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "structured_event", None)
        if isinstance(event, StructuredLogEvent):
            payload = event.to_dict()
        else:
            payload = self._record_to_event(record)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    def _record_to_event(self, record: logging.LogRecord) -> dict[str, Any]:
        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat().replace("+00:00", "Z")
        payload = {
            "ts": ts,
            "level": record.levelname,
            "service": SERVICE_NAME,
            "component": record.name,
            "event_type": getattr(record, "event_type", record.name),
            "message": record.getMessage(),
            "result": getattr(record, "result", "unknown"),
            "status": getattr(record, "status", "unknown"),
        }

        ctx = get_log_context()
        for field_name in ("trace_id", "correlation_id", "job_id", "job_type", "worker_id", "tid", "forum_id", "run_id", "stage"):
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
            elif field_name in ctx:
                payload[field_name] = ctx[field_name]

        for field_name in (
            "error_code",
            "error_message",
            "retryable",
            "attempt",
            "duration_ms",
            "fallback_mode",
            "fallback_reason",
            "warning_codes",
            "agent_hint",
            "next_action",
            "resource_uri",
            "payload",
            "context",
            "tags",
            "data_version",
            "operation",
        ):
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        return payload
