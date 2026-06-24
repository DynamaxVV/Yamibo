from __future__ import annotations

from collections.abc import Callable

from yamibo_mcp.server.agent_tools import PUBLIC_AGENT_TOOLS
from yamibo_mcp.server.job_notifications import (
    JobResourceNotifier,
    enable_resource_subscription_capability,
    register_job_resource_subscriptions,
)
from yamibo_mcp.server.resource_uris import (
    agent_workflows_guide_uri,
    agent_evaluation_guide_uri,
    archive_model_guide_uri,
    error_codes_guide_uri,
    forum_summary_uri,
    forums_index_uri,
    job_events_uri,
    job_status_uri,
    series_chapters_uri,
    series_index_uri,
    thread_assets_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_export_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_summary_uri,
    thread_update_check_uri,
    tools_schema_uri,
)
from yamibo_mcp.server.resources import read_resource_content


def _fastmcp_imports():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "FastMCP SDK is not installed. Install project dependencies first, for example: uv sync"
        ) from exc
    return FastMCP


def build_mcp_server():
    FastMCP = _fastmcp_imports()
    notifier = JobResourceNotifier()
    server = FastMCP(
        name="yamibo-mcp",
        instructions=(
            "Yamibo 本地归档 MCP Server。长操作只返回 job_id；"
            "大文本和二进制内容请通过 resources 读取。"
        ),
    )
    server._yamibo_job_notifier = notifier  # type: ignore[attr-defined]
    register_agent_tools(server)
    register_resources(server)
    register_job_resource_subscriptions(server, notifier)
    enable_resource_subscription_capability(server)
    return server


def register_agent_tools(server) -> None:
    for name, description, handler in PUBLIC_AGENT_TOOLS:
        server.tool(name=name, description=description)(handler)


def _register_text_resource(server, *, name: str, uri_builder: Callable[..., str], mime_type: str, param: str | None = None, cast=int) -> None:
    resource_uri = uri_builder(f"{{{param}}}") if param is not None else uri_builder()

    @server.resource(resource_uri, mime_type=mime_type, name=name)
    def _resource(value: str | None = None):
        uri = uri_builder(cast(value)) if param is not None else uri_builder()
        content, _ = read_resource_content(uri)
        if isinstance(content, bytes):
            return content
        return str(content)


def register_resources(server) -> None:
    specs = [
        ("agent-workflows-guide", agent_workflows_guide_uri, "text/markdown", None, int),
        ("error-codes-guide", error_codes_guide_uri, "text/markdown", None, int),
        ("archive-model-guide", archive_model_guide_uri, "text/markdown", None, int),
        ("agent-evaluation-guide", agent_evaluation_guide_uri, "text/markdown", None, int),
        ("tools-schema", tools_schema_uri, "application/json", None, int),
        ("thread-context", thread_context_uri, "text/markdown", "tid", int),
        ("thread-metadata", thread_metadata_uri, "application/json", "tid", int),
        ("thread-export", thread_export_uri, "application/octet-stream", "tid", int),
        ("series-index", series_index_uri, "text/markdown", None, int),
        ("series-chapters", series_chapters_uri, "application/json", "series_id", int),
        ("forums-index", forums_index_uri, "application/json", None, int),
        ("forum-summary", forum_summary_uri, "application/json", "forum_id", int),
        ("thread-summary", thread_summary_uri, "application/json", "tid", int),
        ("thread-diagnostics", thread_diagnostics_uri, "application/json", "tid", int),
        ("thread-posts", thread_posts_uri, "application/json", "tid", int),
        ("thread-assets", thread_assets_uri, "application/json", "tid", int),
        ("job-status", job_status_uri, "application/json", "job_id", str),
        ("job-events", job_events_uri, "application/json", "job_id", str),
        ("thread-update-check", thread_update_check_uri, "application/json", "tid", int),
    ]
    for name, uri_builder, mime_type, param, cast in specs:
        _register_text_resource(server, name=name, uri_builder=uri_builder, mime_type=mime_type, param=param, cast=cast)
