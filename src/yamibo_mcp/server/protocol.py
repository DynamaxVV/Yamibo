from __future__ import annotations

from typing import Any, Callable

from yamibo_mcp.server.tools import (
    archive_thread,
    browse_forum_page,
    check_thread_updates,
    cleanup_job,
    create_export_thread_job,
    create_noop_job,
    create_sync_thread_job,
    create_update_thread_job,
    export_thread,
    get_job_status,
    get_thread,
    llm_transform_text,
    list_exports,
    parse_thread_title,
    read_resource,
    search_threads,
    sync_forum_range,
)


ToolHandler = Callable[..., Any]

TOOLS: dict[str, tuple[ToolHandler, str]] = {
    "create_noop_job": (create_noop_job, "创建 no-op 后台任务，用于验证任务系统。"),
    "create_sync_thread_job": (create_sync_thread_job, "创建帖子同步任务；当前支持本地 HTML 样例路径。"),
    "create_update_thread_job": (create_update_thread_job, "创建轻小说贴子追加更新任务；先检查再更新。"),
    "create_export_thread_job": (create_export_thread_job, "为已归档帖子创建 ZIP 导出任务。"),
    "update_thread": (create_update_thread_job, "创建轻小说贴子追加更新任务；先检查再更新。"),
    "browse_forum_page": (browse_forum_page, "读取漫画区某一页的帖子列表；短调用，直接返回该页帖子信息。"),
    "archive_thread": (archive_thread, "创建帖子归档任务；长操作仅返回 job_id。"),
    "export_thread": (export_thread, "创建帖子导出任务，支持 cache_only、sync_if_stale、force_resync 策略。"),
    "check_thread_updates": (check_thread_updates, "检查已归档轻小说贴子是否有新更新；只读，不创建任务。"),
    "cleanup_job": (cleanup_job, "创建后台清理任务，支持按 job 清理 staging 或批量清理过期 staging。"),
    "sync_forum_range": (sync_forum_range, "按漫画区页码范围抓取真实帖子列表并批量创建同步任务。"),
    "get_job_status": (get_job_status, "读取后台任务状态。"),
    "search_threads": (search_threads, "统一搜索帖子：优先按论坛页搜索和筛选，再结合本地归档补充详情。"),
    "get_thread": (get_thread, "读取帖子详情；若本地未归档则自动远端抓取并归档后返回。"),
    "list_exports": (list_exports, "列出已经生成的导出包。")
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
            if name not in TOOLS:
                raise ValueError(f"unknown tool: {name}")
            handler = TOOLS[name][0]
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
