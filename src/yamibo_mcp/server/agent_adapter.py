from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.errors import (
    JobNotFound,
    LoginRequiredError,
    RemoteFetchError,
    RemoteMaintenanceError,
    UnexpectedPageError,
)
from yamibo_mcp.storage.exports import ExportPrecheckError


def action_to_wire(action: AgentAction) -> dict[str, Any]:
    return {"tool": action.tool, "args": action.args, "reason": action.reason}


def error_to_wire(error: AgentError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
        "agent_hint": error.agent_hint,
        "retryable": error.retryable,
    }
    if error.field_errors:
        payload["field_errors"] = error.field_errors
    if error.suggested_actions:
        payload["suggested_actions"] = [action_to_wire(action) for action in error.suggested_actions]
    return payload


def to_wire(result: AgentResult) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": result.ok}
    if result.data is not None:
        payload["data"] = result.data
    if result.error is not None:
        payload["error"] = error_to_wire(result.error)
    if result.resources:
        payload["resources"] = result.resources
    if result.next_actions:
        payload["next_actions"] = [action_to_wire(action) for action in result.next_actions]
    if result.warnings:
        payload["warnings"] = result.warnings
    if result.side_effects:
        payload["side_effects"] = result.side_effects
    return payload


def map_exception(exc: Exception) -> AgentResult:
    if isinstance(exc, JobNotFound):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="JOB_NOT_FOUND",
                message=f"Job {exc} was not found.",
                agent_hint="Check the job id and call create_thread_archive_job, create_thread_update_job, or create_thread_export_job again if needed.",
            ),
        )
    if isinstance(exc, LoginRequiredError):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="REMOTE_LOGIN_REQUIRED",
                message=str(exc),
                agent_hint="Provide a valid Yamibo cookie file before calling remote tools.",
            ),
        )
    if isinstance(exc, RemoteMaintenanceError):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="REMOTE_MAINTENANCE",
                message=str(exc),
                agent_hint="Retry the remote call after the forum maintenance window ends.",
                retryable=True,
            ),
        )
    if isinstance(exc, UnexpectedPageError):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="UNEXPECTED_REMOTE_PAGE",
                message=str(exc),
                agent_hint="Retry with a direct thread id or inspect whether the fetched page is a login, maintenance, or redirect page.",
            ),
        )
    if isinstance(exc, RemoteFetchError):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="REMOTE_FETCH_FAILED",
                message=str(exc),
                agent_hint="Retry the remote operation later or fall back to local archive reads if the thread is already archived.",
                retryable=True,
            ),
        )
    if isinstance(exc, ExportPrecheckError):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="EXPORT_PRECHECK_FAILED",
                message=str(exc),
                agent_hint="Ensure the thread is archived completely before creating an export job.",
            ),
        )
    if isinstance(exc, FileNotFoundError):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="LOCAL_ARCHIVE_NOT_FOUND",
                message=str(exc),
                agent_hint="Create or refresh the local archive before reading file-backed views.",
            ),
        )
    if isinstance(exc, ValueError):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message=str(exc),
                agent_hint="Fix the tool arguments and retry.",
            ),
        )
    return AgentResult(
        ok=False,
        error=AgentError(
            code="INTERNAL_ERROR",
            message=str(exc),
            agent_hint="Treat this as an internal server failure and retry only after checking logs or narrowing the request.",
            retryable=False,
        ),
    )


def agent_tool(fn: Callable[..., AgentResult]) -> Callable[..., dict[str, Any]]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return to_wire(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            return to_wire(map_exception(exc))

    return wrapper
