from __future__ import annotations

from typing import Any, Callable

from yamibo_mcp.server.agent_tools import (
    browse_forum_page,
    check_thread_updates,
    create_thread_archive_job,
    create_thread_export_job,
    create_thread_update_job,
    ensure_thread_archived,
    inspect_remote_thread,
    read_archived_thread,
    read_forum_profiles,
    read_job,
    read_job_events,
    search_forum_threads,
)
from yamibo_mcp.server.tools import (
    archive_thread as legacy_archive_thread,
    export_thread as legacy_export_thread,
    get_job_status as legacy_get_job_status,
    get_thread as legacy_get_thread,
    read_resource,
    search_threads as legacy_search_threads,
    update_thread as legacy_update_thread,
)


ToolHandler = Callable[..., Any]

TOOLS: dict[str, tuple[ToolHandler, str]] = {
    "browse_forum_page": (browse_forum_page, "Remote read-only forum page browse; does not create jobs."),
    "search_forum_threads": (search_forum_threads, "Remote-first forum search with compact archive hints; does not expose limit."),
    "inspect_remote_thread": (inspect_remote_thread, "Remote read-only thread preview; never writes SQLite or materialized files."),
    "create_thread_archive_job": (create_thread_archive_job, "Create a background archive job and return the job id."),
    "ensure_thread_archived": (ensure_thread_archived, "Check whether a thread is archived locally; create an archive job if missing."),
    "read_archived_thread": (read_archived_thread, "Read compact local archive views from SQLite and materialized files."),
    "check_thread_updates": (check_thread_updates, "Inspect novel-thread updates without creating a job."),
    "create_thread_update_job": (create_thread_update_job, "Create a background incremental update job for an archived novel thread."),
    "create_thread_export_job": (create_thread_export_job, "Create a background export job for a local archive."),
    "read_job": (read_job, "Read compact job status from the local queue."),
    "read_job_events": (read_job_events, "Read persisted job event history from the local queue."),
    "read_forum_profiles": (read_forum_profiles, "Read configured forum profiles from local metadata."),
}

COMPAT_TOOLS: dict[str, ToolHandler] = {
    "search_threads": legacy_search_threads,
    "archive_thread": legacy_archive_thread,
    "export_thread": legacy_export_thread,
    "get_job_status": legacy_get_job_status,
    "get_thread": legacy_get_thread,
    "update_thread": legacy_update_thread,
}


def list_tools_payload() -> dict[str, object]:
    return {
        "tools": [
            {
                "name": name,
                "description": description,
            }
            for name, (_, description) in TOOLS.items()
        ]
    }


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    # 这里用最小 JSON-RPC/stdio 语义先把 Server 边界立起来，
    # 后续接 FastMCP 时，业务调用层可以直接复用。
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    try:
        if method == "initialize":
            return {
                "id": request_id,
                "result": {
                    "server": "yamibo-mcp",
                    "protocol": "yamibo-jsonrpc-v1",
                    "capabilities": {
                        "tools": True,
                        "resources": True,
                    },
                },
            }
        if method == "ping":
            return {"id": request_id, "result": {"ok": True}}
        if method == "tools/list":
            return {"id": request_id, "result": list_tools_payload()}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            handler = TOOLS.get(name, (COMPAT_TOOLS.get(name), ""))[0]
            if handler is None:
                raise ValueError(f"unknown tool: {name}")
            result = handler(**arguments)
            return {"id": request_id, "result": result}
        if method == "resources/read":
            uri = params.get("uri")
            if not uri:
                raise ValueError("resources/read requires uri")
            return {"id": request_id, "result": read_resource(uri)}
        raise ValueError(f"unsupported method: {method}")
    except Exception as exc:  # noqa: BLE001 - 协议边界统一转错误响应
        return {
            "id": request_id,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }
