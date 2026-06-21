from __future__ import annotations

import argparse
import json

from yamibo_mcp.logging import configure_logging
from yamibo_mcp.server.protocol import handle_request
from yamibo_mcp.server.resources import (
    forums_index_uri,
    forum_summary_uri,
    job_events_uri,
    series_chapters_uri,
    series_index_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_export_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_assets_uri,
    thread_summary_uri,
    thread_update_check_uri,
)
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
    archive_thread,
    cleanup_job,
    create_noop_job,
    dump_json,
    export_thread,
    get_job_status,
    get_thread,
    list_exports,
    read_resource,
    read_resource_content,
    search_threads,
    sync_forum_range,
    update_thread,
)


def _fastmcp_imports():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出清晰提示
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

    @server.tool(name="cleanup_job", description="创建后台清理任务，支持按 job 清理 staging 或批量清理过期 staging。")
    def _cleanup_job(
        job_id: str | None = None,
        mode: str = "job_staging",
        older_than_hours: int | None = None,
    ) -> dict[str, object]:
        return cleanup_job(job_id=job_id, mode=mode, older_than_hours=older_than_hours)

    @server.tool(name="sync_forum_range", description="Legacy bulk job creator. Prefer browse_forum_page plus create_thread_archive_job for agent-facing flows.")
    def _sync_forum_range(
        start_page: int,
        end_page: int,
        forum_id: int = 30,
        base_url: str = "https://bbs.yamibo.com",
        cookie_file: str | None = None,
        include_sticky: bool = False,
        include_announcements: bool = False,
    ) -> dict[str, object]:
        return sync_forum_range(
            start_page=start_page,
            end_page=end_page,
            forum_id=forum_id,
            base_url=base_url,
            cookie_file=cookie_file,
            include_sticky=include_sticky,
            include_announcements=include_announcements,
        )

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

    return server


def _serve_legacy_stdio() -> None:
    # 保留旧 JSON-RPC 协议，方便现有测试和平滑迁移；正式 MCP 入口走 FastMCP stdio。
    import sys

    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            response = {
                "id": None,
                "error": {
                    "type": "JSONDecodeError",
                    "message": str(exc),
                },
            }
        else:
            response = handle_request(request)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Yamibo MCP server.")
    sub = parser.add_subparsers(dest="command")
    stdio_parser = sub.add_parser("stdio")
    stdio_parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    sub.add_parser("serve-legacy-stdio")
    sub.add_parser("create-noop-job")
    sync_parser = sub.add_parser("create-sync-thread-job")
    sync_parser.add_argument("--html-path")
    sync_parser.add_argument("--tid", type=int)
    sync_parser.add_argument("--url")
    sync_parser.add_argument("--base-url")
    browse_forum_page_parser = sub.add_parser("browse-forum-page")
    browse_forum_page_parser.add_argument("--page", type=int, required=True)
    browse_forum_page_parser.add_argument("--forum-id", type=int, default=30)
    browse_forum_page_parser.add_argument("--order", default="default", choices=["default", "dateline"], help="排序方式: default=最后回复, dateline=发帖时间")
    browse_forum_page_parser.add_argument("--base-url", default="https://bbs.yamibo.com")
    browse_forum_page_parser.add_argument("--cookie-file")
    browse_forum_page_parser.add_argument("--include-sticky", action="store_true")
    browse_forum_page_parser.add_argument("--include-announcements", action="store_true")
    update_check_parser = sub.add_parser("check-thread-updates")
    update_check_parser.add_argument("--tid", type=int, required=True)
    update_check_parser.add_argument("--base-url")
    forum_range_parser = sub.add_parser("create-sync-forum-range-jobs")
    forum_range_parser.add_argument("--start-page", type=int, required=True)
    forum_range_parser.add_argument("--end-page", type=int, required=True)
    forum_range_parser.add_argument("--forum-id", type=int, default=30)
    forum_range_parser.add_argument("--base-url", default="https://bbs.yamibo.com")
    forum_range_parser.add_argument("--cookie-file")
    forum_range_parser.add_argument("--include-sticky", action="store_true")
    forum_range_parser.add_argument("--include-announcements", action="store_true")
    export_parser = sub.add_parser("create-export-thread-job")
    export_parser.add_argument("--tid", type=int, required=True)
    export_parser.add_argument("--strategy")
    update_parser = sub.add_parser("update-thread")
    update_parser.add_argument("--tid", type=int, required=True)
    update_parser.add_argument("--base-url")
    search_parser = sub.add_parser("search-threads")
    search_parser.add_argument("--query", default="")
    search_parser.add_argument("--forum-id", type=int, default=30)
    search_parser.add_argument("--limit", default=100)
    search_parser.add_argument("--start-page", type=int, default=1)
    search_parser.add_argument("--end-page", type=int)
    search_parser.add_argument("--posted-on", help="按发帖日期搜索(YYYY-MM-DD)，与 --start-page/--end-page 互斥")
    search_parser.add_argument("--base-url", default="https://bbs.yamibo.com")
    search_parser.add_argument("--cookie-file")
    search_parser.add_argument("--include-sticky", action="store_true")
    search_parser.add_argument("--include-announcements", action="store_true")
    get_thread_parser = sub.add_parser("get-thread")
    get_thread_parser.add_argument("--tid", type=int, required=True)
    get_thread_parser.add_argument("--url")
    get_thread_parser.add_argument("--base-url")
    sub.add_parser("list-exports")
    read_resource_parser = sub.add_parser("read-resource")
    read_resource_parser.add_argument("uri")
    cleanup_parser = sub.add_parser("cleanup-job")
    cleanup_parser.add_argument("job_id", nargs="?")
    cleanup_parser.add_argument("--mode", default="job_staging")
    cleanup_parser.add_argument("--older-than-hours", type=int)
    status_parser = sub.add_parser("job-status")
    status_parser.add_argument("job_id")
    args = parser.parse_args()
    configure_logging()

    command = args.command or "stdio"
    if command == "stdio":
        build_mcp_server().run(transport=args.transport)
    elif command == "serve-legacy-stdio":
        _serve_legacy_stdio()
    elif command == "create-noop-job":
        print(create_noop_job())
    elif command == "create-sync-thread-job":
        print(dump_json(archive_thread(html_path=args.html_path, tid=args.tid, url=args.url, base_url=args.base_url)))
    elif command == "browse-forum-page":
        print(
            dump_json(
                browse_forum_page(
                    page=args.page,
                    forum_id=args.forum_id,
                    order=args.order,
                    base_url=args.base_url,
                    cookie_file=args.cookie_file,
                    include_sticky=args.include_sticky,
                    include_announcements=args.include_announcements,
                )
            )
        )
    elif command == "create-sync-forum-range-jobs":
        print(
            dump_json(
                sync_forum_range(
                    start_page=args.start_page,
                    end_page=args.end_page,
                    forum_id=args.forum_id,
                    base_url=args.base_url,
                    cookie_file=args.cookie_file,
                    include_sticky=args.include_sticky,
                    include_announcements=args.include_announcements,
                )
            )
        )
    elif command == "create-export-thread-job":
        print(dump_json(export_thread(tid=args.tid, strategy=args.strategy)))
    elif command == "update-thread":
        print(dump_json(update_thread(tid=args.tid, base_url=args.base_url)))
    elif command == "check-thread-updates":
        print(dump_json(check_thread_updates(tid=args.tid, base_url=args.base_url)))
    elif command == "search-threads":
        print(
            dump_json(
                search_threads(
                    query=args.query,
                    forum_id=args.forum_id,
                    limit=args.limit,
                    start_page=args.start_page,
                    end_page=args.end_page,
                    posted_on=args.posted_on,
                    base_url=args.base_url,
                    cookie_file=args.cookie_file,
                    include_sticky=args.include_sticky,
                    include_announcements=args.include_announcements,
                )
            )
        )
    elif command == "get-thread":
        print(dump_json(get_thread(tid=args.tid, url=args.url, base_url=args.base_url)))
    elif command == "list-exports":
        print(dump_json(list_exports()))
    elif command == "read-resource":
        print(dump_json(read_resource(args.uri)))
    elif command == "cleanup-job":
        print(dump_json(cleanup_job(job_id=args.job_id, mode=args.mode, older_than_hours=args.older_than_hours)))
    elif command == "job-status":
        print(dump_json(get_job_status(args.job_id)))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
