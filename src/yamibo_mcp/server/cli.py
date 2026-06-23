from __future__ import annotations

import argparse
import json
import sys

from yamibo_mcp.logging import configure_logging
from yamibo_mcp.server.agent_tools import (
    create_rag_index_batch_jobs,
    create_rag_index_job,
    create_thread_archive_batch_jobs,
    search_archived_content,
)
from yamibo_mcp.server.legacy_protocol import handle_request
from yamibo_mcp.server.mcp_registry import build_mcp_server
from yamibo_mcp.server.legacy_tools import (
    archive_thread,
    browse_forum_page,
    check_thread_updates,
    cleanup_job,
    create_noop_job,
    dump_json,
    export_thread,
    get_job_status,
    get_thread,
    list_exports,
    read_resource,
    search_threads,
    sync_forum_range,
    update_thread,
)


def serve_legacy_stdio() -> None:
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


def build_parser() -> argparse.ArgumentParser:
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
    rag_index_parser = sub.add_parser("create-rag-index-job")
    rag_index_parser.add_argument("--tid", type=int)
    rag_index_parser.add_argument("--force", action="store_true")
    rag_index_parser.add_argument("--embedding-dimensions", type=int)
    archive_batch_parser = sub.add_parser("create-sync-thread-batch-jobs")
    archive_batch_parser.add_argument("--tid", type=int, action="append", dest="tids", required=True)
    archive_batch_parser.add_argument("--base-url")
    archive_batch_parser.add_argument("--forum-id", type=int)
    rag_index_batch_parser = sub.add_parser("create-rag-index-batch-jobs")
    rag_index_batch_parser.add_argument("--tid", type=int, action="append", dest="tids", required=True)
    rag_index_batch_parser.add_argument("--force", action="store_true")
    rag_index_batch_parser.add_argument("--embedding-dimensions", type=int)
    rag_search_parser = sub.add_parser("search-archived-content")
    rag_search_parser.add_argument("--query", required=True)
    rag_search_parser.add_argument("--mode", default="hybrid", choices=["hybrid", "keyword", "vector"])
    rag_search_parser.add_argument("--top-k", type=int, default=10)
    rag_search_parser.add_argument("--forum-id", type=int)
    rag_search_parser.add_argument("--content-kind")
    rag_search_parser.add_argument("--tid", type=int)
    rag_search_parser.add_argument("--series-id", type=int)
    rag_search_parser.add_argument("--floor-start", type=int)
    rag_search_parser.add_argument("--floor-end", type=int)
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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging()

    command = args.command or "stdio"
    if command == "stdio":
        build_mcp_server().run(transport=args.transport)
    elif command == "serve-legacy-stdio":
        serve_legacy_stdio()
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
    elif command == "create-rag-index-job":
        print(
            dump_json(
                create_rag_index_job(
                    tid=args.tid,
                    force=args.force,
                    embedding_dimensions=args.embedding_dimensions,
                )
            )
        )
    elif command == "create-sync-thread-batch-jobs":
        print(
            dump_json(
                create_thread_archive_batch_jobs(
                    tids=args.tids,
                    base_url=args.base_url,
                    forum_id=args.forum_id,
                )
            )
        )
    elif command == "create-rag-index-batch-jobs":
        print(
            dump_json(
                create_rag_index_batch_jobs(
                    tids=args.tids,
                    force=args.force,
                    embedding_dimensions=args.embedding_dimensions,
                )
            )
        )
    elif command == "search-archived-content":
        print(
            dump_json(
                search_archived_content(
                    query=args.query,
                    mode=args.mode,
                    top_k=args.top_k,
                    forum_id=args.forum_id,
                    content_kind=args.content_kind,
                    tid=args.tid,
                    series_id=args.series_id,
                    floor_start=args.floor_start,
                    floor_end=args.floor_end,
                )
            )
        )
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
