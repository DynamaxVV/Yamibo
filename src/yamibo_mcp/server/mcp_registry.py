from __future__ import annotations

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
from yamibo_mcp.server.resources import (
    forum_summary_uri,
    forums_index_uri,
    job_events_uri,
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
)
from yamibo_mcp.server.resource_handlers import read_resource_content


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
    server = FastMCP(
        name="yamibo-mcp",
        instructions=(
            "Yamibo 本地归档 MCP Server。长操作只返回 job_id；"
            "大文本和二进制内容请通过 resources 读取。"
        ),
    )
    register_agent_tools(server)
    register_resources(server)
    return server


def register_agent_tools(server) -> None:
    @server.tool(name="search_forum_threads", description="Remote-first, read-only forum search. Uses forum pagination and compact archive hints; does not expose a limit parameter.")
    def _search_forum_threads(
        query: str = "",
        forum_id: int = 30,
        start_page: int = 1,
        end_page: int | None = None,
        posted_on: str | None = None,
        base_url: str = "https://bbs.yamibo.com",
        cookie_file: str | None = None,
        include_sticky: bool = False,
        include_announcements: bool = False,
    ) -> dict[str, object]:
        return search_forum_threads(
            query=query,
            forum_id=forum_id,
            start_page=start_page,
            end_page=end_page,
            posted_on=posted_on,
            base_url=base_url,
            cookie_file=cookie_file,
            include_sticky=include_sticky,
            include_announcements=include_announcements,
        )

    @server.tool(name="browse_forum_page", description="Remote read-only forum page browse. Returns one page of compact thread items and never creates jobs.")
    def _browse_forum_page(
        page: int,
        forum_id: int = 30,
        order: str = "default",
        base_url: str = "https://bbs.yamibo.com",
        cookie_file: str | None = None,
        include_sticky: bool = False,
        include_announcements: bool = False,
    ) -> dict[str, object]:
        return browse_forum_page(
            page=page,
            forum_id=forum_id,
            order=order,
            base_url=base_url,
            cookie_file=cookie_file,
            include_sticky=include_sticky,
            include_announcements=include_announcements,
        )

    @server.tool(name="inspect_remote_thread", description="Remote read-only thread preview. Fetches and parses a compact snapshot without writing SQLite, downloading assets, or creating jobs.")
    def _inspect_remote_thread(
        tid: int,
        forum_id: int | None = None,
        author_only: bool = False,
        base_url: str | None = None,
    ) -> dict[str, object]:
        return inspect_remote_thread(tid=tid, forum_id=forum_id, author_only=author_only, base_url=base_url)

    @server.tool(name="create_thread_archive_job", description="Create a background archive job for a thread or local HTML input. Side effect: writes a queued job to SQLite; daemon execution is required.")
    def _create_thread_archive_job(
        html_path: str | None = None,
        tid: int | None = None,
        url: str | None = None,
        base_url: str | None = None,
        forum_id: int | None = None,
    ) -> dict[str, object]:
        return create_thread_archive_job(html_path=html_path, tid=tid, url=url, base_url=base_url, forum_id=forum_id)

    @server.tool(name="ensure_thread_archived", description="Local archive check plus job creation fallback. Returns local archive state if present, otherwise creates an archive job.")
    def _ensure_thread_archived(tid: int, base_url: str | None = None, forum_id: int | None = None) -> dict[str, object]:
        return ensure_thread_archived(tid=tid, base_url=base_url, forum_id=forum_id)

    @server.tool(name="read_archived_thread", description="Read local archive views from SQLite and materialized files. Views are local-only and never trigger remote fetches.")
    def _read_archived_thread(
        tid: int,
        view: str,
        floor_start: int | None = None,
        floor_end: int | None = None,
    ) -> dict[str, object]:
        return read_archived_thread(tid=tid, view=view, floor_start=floor_start, floor_end=floor_end)

    @server.tool(name="check_thread_updates", description="Remote read-only update inspection for archived novel threads. Does not create jobs.")
    def _check_thread_updates(tid: int, base_url: str | None = None) -> dict[str, object]:
        return check_thread_updates(tid=tid, base_url=base_url)

    @server.tool(name="create_thread_update_job", description="Create a background incremental update job for an archived novel thread. Side effect: writes a queued job to SQLite.")
    def _create_thread_update_job(tid: int, base_url: str | None = None) -> dict[str, object]:
        return create_thread_update_job(tid=tid, base_url=base_url)

    @server.tool(name="create_thread_export_job", description="Create a background export job for a local archive. Side effect: writes a queued job to SQLite.")
    def _create_thread_export_job(tid: int, strategy: str | None = None) -> dict[str, object]:
        return create_thread_export_job(tid=tid, strategy=strategy)

    @server.tool(name="read_job", description="Read compact job status from the local SQLite queue.")
    def _read_job(job_id: str) -> dict[str, object]:
        return read_job(job_id=job_id)

    @server.tool(name="read_job_events", description="Read persisted job event history for a queued or completed job.")
    def _read_job_events(job_id: str) -> dict[str, object]:
        return read_job_events(job_id=job_id)

    @server.tool(name="read_forum_profiles", description="Read configured forum profiles and content-type guidance from local metadata.")
    def _read_forum_profiles() -> dict[str, object]:
        return read_forum_profiles()


def register_resources(server) -> None:
    @server.resource(thread_context_uri("{tid}"), mime_type="text/markdown", name="thread-context")
    def _thread_context(tid: str) -> str:
        content, _ = read_resource_content(thread_context_uri(int(tid)))
        return str(content)

    @server.resource(thread_metadata_uri("{tid}"), mime_type="application/json", name="thread-metadata")
    def _thread_metadata(tid: str) -> str:
        content, _ = read_resource_content(thread_metadata_uri(int(tid)))
        return str(content)

    @server.resource(thread_export_uri("{tid}"), mime_type="application/octet-stream", name="thread-export")
    def _thread_export(tid: str) -> bytes:
        content, _ = read_resource_content(thread_export_uri(int(tid)))
        if isinstance(content, bytes):
            return content
        return content.encode("utf-8")

    @server.resource(series_index_uri(), mime_type="text/markdown", name="series-index")
    def _series_index() -> str:
        content, _ = read_resource_content(series_index_uri())
        return str(content)

    @server.resource(series_chapters_uri("{series_id}"), mime_type="application/json", name="series-chapters")
    def _series_chapters(series_id: str) -> str:
        content, _ = read_resource_content(series_chapters_uri(int(series_id)))
        return str(content)

    @server.resource(forums_index_uri(), mime_type="application/json", name="forums-index")
    def _forums_index() -> str:
        content, _ = read_resource_content(forums_index_uri())
        return str(content)

    @server.resource(forum_summary_uri("{forum_id}"), mime_type="application/json", name="forum-summary")
    def _forum_summary(forum_id: str) -> str:
        content, _ = read_resource_content(forum_summary_uri(int(forum_id)))
        return str(content)

    @server.resource(thread_summary_uri("{tid}"), mime_type="application/json", name="thread-summary")
    def _thread_summary(tid: str) -> str:
        content, _ = read_resource_content(thread_summary_uri(int(tid)))
        return str(content)

    @server.resource(thread_diagnostics_uri("{tid}"), mime_type="application/json", name="thread-diagnostics")
    def _thread_diagnostics(tid: str) -> str:
        content, _ = read_resource_content(thread_diagnostics_uri(int(tid)))
        return str(content)

    @server.resource(thread_posts_uri("{tid}"), mime_type="application/json", name="thread-posts")
    def _thread_posts(tid: str) -> str:
        content, _ = read_resource_content(thread_posts_uri(int(tid)))
        return str(content)

    @server.resource(thread_assets_uri("{tid}"), mime_type="application/json", name="thread-assets")
    def _thread_assets(tid: str) -> str:
        content, _ = read_resource_content(thread_assets_uri(int(tid)))
        return str(content)

    @server.resource(thread_update_check_uri("{tid}"), mime_type="application/json", name="thread-update-check")
    def _thread_update_check(tid: str) -> str:
        content, _ = read_resource_content(thread_update_check_uri(int(tid)))
        return str(content)

    @server.resource(job_events_uri("{job_id}"), mime_type="application/json", name="job-events")
    def _job_events(job_id: str) -> str:
        content, _ = read_resource_content(job_events_uri(job_id))
        return str(content)
