from __future__ import annotations

import argparse
import inspect
import json
import sys
from datetime import date, datetime

from yamibo_mcp.application.archive_commands import (
    archive_thread_job,
    sync_forum_range,
)
from yamibo_mcp.application.thread_preview import preview_thread_context
from yamibo_mcp.application.archive_queries import list_exports
from yamibo_mcp.application.thread_resync_planner import plan_thread_resync_batch
from yamibo_mcp.application.thread_preview import preview_thread_context
from yamibo_mcp.application.discussion_trend_commands import (
    create_discussion_trend_index_job as _create_discussion_trend_index_job,
)
from yamibo_mcp.application.discussion_report import (
    create_discussion_trend_report_job as _create_discussion_trend_report_job,
    create_forum_research_report_job as _create_forum_research_report_job,
)
from yamibo_mcp.application.discussion_trend_queries import (
    get_discussion_partition_trends as _get_discussion_partition_trends,
    get_discussion_topic_trends as _get_discussion_topic_trends,
    get_discussion_user_trends as _get_discussion_user_trends,
    get_discussion_report as _get_discussion_report,
    get_discussion_topic_evidence as _get_discussion_topic_evidence,
    get_forum_evidence_pack as _get_forum_evidence_pack,
)
from yamibo_mcp.application.job_queries import get_job_status_payload
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.maintenance.cleanup_data import cleanup_orphan_thread_dirs
from yamibo_mcp.server.agent_tools import (
    create_thread_archive_job,
    create_rag_index_batch_jobs,
    create_rag_index_job,
    create_thread_archive_batch_jobs,
    ensure_thread_archived,
    inspect_remote_thread,
    probe_archived_threads,
    read_job_events,
    search_archived_content,
    wait_for_job,
)
from yamibo_mcp.server.agent_adapter import to_wire
from yamibo_mcp.server.mcp_registry import build_mcp_server
from yamibo_mcp.server.resources import read_resource
from yamibo_mcp.application.remote_queries import (
    browse_forum_page,
    refresh_forum_remote_observations,
    search_threads,
)
from yamibo_mcp.application.update_commands import create_update_thread_job
from yamibo_mcp.application.update_queries import check_thread_updates
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.yamibo.proxy_pool import check_proxy_pool_health


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def dump_json(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)


def create_noop_job() -> str:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        job = JobsRepository(conn).create(JobType.NOOP.value)
        return job.job_id
    finally:
        conn.close()


def cleanup_job(*, job_id: str | None = None, mode: str = "job_staging", older_than_hours: int | None = None) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        payload = {"mode": mode}
        if job_id is not None:
            payload["job_id"] = job_id
        if older_than_hours is not None:
            payload["older_than_hours"] = older_than_hours
        job = JobsRepository(conn).create(JobType.CLEANUP_JOB.value, payload=payload)
        return {"job_id": job.job_id}
    finally:
        conn.close()


def cleanup_orphan_threads(*, dry_run: bool = False) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        removed = cleanup_orphan_thread_dirs(data_dir=settings.data_dir, conn=conn, dry_run=dry_run)
        return {"removed_count": len(removed), "paths": [str(path) for path in removed], "dry_run": dry_run}
    finally:
        conn.close()


def backfill_thread_metadata(*, overwrite_last_reply: bool = False) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        result = ThreadsRepository(conn).backfill_local_reply_metadata(overwrite_last_reply=overwrite_last_reply)
        conn.commit()
        return result
    finally:
        conn.close()


def export_thread(*, tid: int, strategy: str | None = None) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        payload = {"tid": tid}
        if strategy:
            payload["strategy"] = strategy
        job = JobsRepository(conn).create(JobType.EXPORT_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id}
    finally:
        conn.close()


def update_thread(*, tid: int, base_url: str | None = None) -> dict[str, object]:
    return create_update_thread_job(tid=tid, base_url=base_url)


def _add_trend_query_parser(sub, name: str, help_text: str) -> argparse.ArgumentParser:
    p = sub.add_parser(name, help=help_text)
    p.add_argument("--forum-id", type=int, required=True)
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--version", default="trend-v1")
    return p


def _run_discussion_partition_trends(args: argparse.Namespace):
    return _get_discussion_partition_trends(
        forum_id=args.forum_id,
        start_date=args.start_date,
        end_date=args.end_date,
        version=args.version,
    )


def _run_discussion_topic_trends(args: argparse.Namespace):
    return _get_discussion_topic_trends(
        forum_id=args.forum_id,
        start_date=args.start_date,
        end_date=args.end_date,
        version=args.version,
    )


def _run_discussion_user_trends(args: argparse.Namespace):
    return _get_discussion_user_trends(
        forum_id=args.forum_id,
        start_date=args.start_date,
        end_date=args.end_date,
        version=args.version,
    )


def _run_discussion_report(args: argparse.Namespace):
    return _get_discussion_report(
        forum_id=args.forum_id,
        start_date=args.start_date,
        end_date=args.end_date,
        version=args.version,
    )


def _run_forum_evidence_pack(args: argparse.Namespace):
    return _get_forum_evidence_pack(
        forum_id=args.forum_id,
        query=args.query,
        start_date=args.start_date,
        end_date=args.end_date,
        top_k=args.top_k,
        mode=args.mode,
        intent=args.intent,
        require_current_run=args.require_current_run,
    )


def _run_discussion_topic_evidence(args: argparse.Namespace):
    return _get_discussion_topic_evidence(
        forum_id=args.forum_id,
        start_date=args.start_date,
        end_date=args.end_date,
        version=args.version,
        topic_id=args.topic_id,
        topic_label=args.topic_label,
        top_k=args.top_k,
        mode=args.mode,
    )


_TREND_QUERY_SPECS = (
    ("discussion-partition-trends", "Query partition-level daily activity trends", _add_trend_query_parser, _run_discussion_partition_trends),
    ("discussion-topic-trends", "Query topic-level daily trends", _add_trend_query_parser, _run_discussion_topic_trends),
    ("discussion-user-trends", "Query user-level daily trends", _add_trend_query_parser, _run_discussion_user_trends),
    ("discussion-report", "Query generated report artifacts", _add_trend_query_parser, _run_discussion_report),
)


def _build_forum_evidence_pack_parser(sub) -> argparse.ArgumentParser:
    parser = sub.add_parser("forum-evidence-pack")
    parser.add_argument("--forum-id", type=int, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--mode", default="auto", choices=["auto", "sql", "rag"])
    parser.add_argument("--intent", default="general_research")
    parser.add_argument("--require-current-run", action="store_true")
    return parser


def _build_discussion_topic_evidence_parser(sub) -> argparse.ArgumentParser:
    parser = _add_trend_query_parser(sub, "discussion-topic-evidence", "Query topic evidence snippets")
    parser.add_argument("--topic-id", type=int, default=None)
    parser.add_argument("--topic-label", default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--mode", default="auto", choices=["auto", "sql", "rag"])
    return parser


_TREND_QUERY_RUNNERS = {
    name: runner for name, _, _, runner in _TREND_QUERY_SPECS
}
_TREND_QUERY_RUNNERS["forum-evidence-pack"] = _run_forum_evidence_pack
_TREND_QUERY_RUNNERS["discussion-topic-evidence"] = _run_discussion_topic_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Yamibo Archive server.")
    sub = parser.add_subparsers(dest="command")
    stdio_parser = sub.add_parser("stdio")
    stdio_parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    stdio_parser.add_argument("--host", default="0.0.0.0")
    stdio_parser.add_argument("--port", type=int, default=8000)
    stdio_parser.add_argument("--path", default="/sse")
    sub.add_parser("create-noop-job")
    sync_parser = sub.add_parser("create-sync-thread-job")
    sync_parser.add_argument("--html-path")
    sync_parser.add_argument("--tid", type=int)
    sync_parser.add_argument("--url")
    sync_parser.add_argument("--base-url")
    sync_parser.add_argument("--forum-id", type=int)
    archive_parser = sub.add_parser("create-thread-archive-job")
    archive_parser.add_argument("--html-path")
    archive_parser.add_argument("--tid", type=int)
    archive_parser.add_argument("--url")
    archive_parser.add_argument("--base-url")
    archive_parser.add_argument("--forum-id", type=int)
    inspect_parser = sub.add_parser("inspect-remote-thread")
    inspect_parser.add_argument("--tid", type=int, required=True)
    inspect_parser.add_argument("--forum-id", type=int)
    inspect_parser.add_argument("--base-url")
    browse_forum_page_parser = sub.add_parser("browse-forum-page")
    browse_forum_page_parser.add_argument("--page", type=int, required=True)
    browse_forum_page_parser.add_argument("--forum-id", type=int, default=30)
    browse_forum_page_parser.add_argument("--order", default="default", choices=["default", "dateline"], help="排序方式: default=最后回复, dateline=发帖时间")
    browse_forum_page_parser.add_argument("--base-url", default="https://bbs.yamibo.com")
    browse_forum_page_parser.add_argument("--cookie-file")
    browse_forum_page_parser.add_argument("--include-sticky", action="store_true")
    browse_forum_page_parser.add_argument("--include-announcements", action="store_true")
    refresh_parser = sub.add_parser("refresh-forum-remote-observations")
    refresh_parser.add_argument("--forum-id", type=int, default=30)
    refresh_parser.add_argument("--page-start", type=int, default=1)
    refresh_parser.add_argument("--page-end", type=int)
    refresh_parser.add_argument("--order", default="default", choices=["default", "dateline"])
    refresh_parser.add_argument("--base-url", default="https://bbs.yamibo.com")
    refresh_parser.add_argument("--cookie-file")
    refresh_parser.add_argument("--include-sticky", action="store_true")
    refresh_parser.add_argument("--include-announcements", action="store_true")
    refresh_parser.add_argument("--dry-run", action="store_true")
    preview_parser = sub.add_parser("preview-thread-context")
    preview_parser.add_argument("--tid", type=int, required=True)
    preview_parser.add_argument("--format", dest="context_format_version", default="obsidian-md-v2", choices=["archive-md-v1", "obsidian-md-v2"])
    plan_parser = sub.add_parser("plan-thread-resync-batch")
    plan_parser.add_argument("--forum-id", type=int)
    plan_parser.add_argument("--page", type=int, action="append", dest="pages")
    plan_parser.add_argument("--pages", type=int, action="append", dest="pages")
    plan_parser.add_argument("--tid", type=int, action="append", dest="tids")
    plan_parser.add_argument("--order", default="default", choices=["default", "dateline"])
    plan_parser.add_argument("--mode", default="daily_delta", choices=["backfill", "daily_delta"])
    plan_parser.add_argument("--force", action="store_true")
    plan_parser.add_argument("--include-unknown", action="store_true")
    plan_parser.add_argument("--max-detail-jobs", type=int)
    plan_parser.add_argument("--persist-observation", action="store_true")
    plan_parser.add_argument("--dry-run", action="store_true")
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
    probe_parser = sub.add_parser("probe-archived-threads")
    probe_parser.add_argument("--tid", type=int, action="append", dest="tids", required=True)
    ensure_parser = sub.add_parser("ensure-thread-archived")
    ensure_parser.add_argument("--tid", type=int, required=True)
    ensure_parser.add_argument("--base-url")
    ensure_parser.add_argument("--forum-id", type=int)
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
    sub.add_parser("list-exports")
    read_resource_parser = sub.add_parser("read-resource")
    read_resource_parser.add_argument("uri")
    cleanup_parser = sub.add_parser("cleanup-job")
    cleanup_parser.add_argument("job_id", nargs="?")
    cleanup_parser.add_argument("--mode", default="job_staging")
    cleanup_parser.add_argument("--older-than-hours", type=int)
    orphan_cleanup_parser = sub.add_parser("cleanup-orphan-thread-dirs")
    orphan_cleanup_parser.add_argument("--dry-run", action="store_true")
    backfill_thread_meta_parser = sub.add_parser("backfill-thread-metadata")
    backfill_thread_meta_parser.add_argument("--overwrite-last-reply", action="store_true")
    status_parser = sub.add_parser("job-status")
    status_parser.add_argument("job_id")
    job_events_parser = sub.add_parser("read-job-events")
    job_events_parser.add_argument("job_id")
    wait_job_parser = sub.add_parser("wait-for-job")
    wait_job_parser.add_argument("job_id")
    wait_job_parser.add_argument("--timeout-seconds", type=float, default=120)
    wait_job_parser.add_argument("--poll-interval-seconds", type=float, default=2)
    wait_job_parser.add_argument("--include-events", action="store_true")
    sub.add_parser("check-proxy-pool")
    trend_parser = sub.add_parser("create-discussion-trend-index-job")
    trend_parser.add_argument("--forum-id", type=int, required=True)
    trend_parser.add_argument("--start-date", required=True, help="ISO date YYYY-MM-DD (inclusive)")
    trend_parser.add_argument("--end-date", required=True, help="ISO date YYYY-MM-DD (inclusive)")
    trend_parser.add_argument("--version", default="trend-v1")
    trend_parser.add_argument("--retention-success-runs", type=int, default=3)
    trend_parser.add_argument(
        "--thresholds-json",
        default=None,
        help='Optional JSON object overriding default topic thresholds, e.g. \'{"min_floor_count": 10}\'',
    )
    # Step 05 report job subcommands
    trend_report_parser = sub.add_parser("create-discussion-trend-report-job")
    trend_report_parser.add_argument("--forum-id", type=int, required=True)
    trend_report_parser.add_argument("--start-date", required=True, help="ISO date YYYY-MM-DD")
    trend_report_parser.add_argument("--end-date", required=True, help="ISO date YYYY-MM-DD")
    trend_report_parser.add_argument("--version", default="trend-v1")
    trend_report_parser.add_argument("--period", default="monthly", choices=["daily", "monthly", "custom"])
    research_parser = sub.add_parser("create-forum-research-report-job")
    research_parser.add_argument("--forum-id", type=int, required=True)
    research_parser.add_argument("--start-date", required=True, help="ISO date YYYY-MM-DD")
    research_parser.add_argument("--end-date", required=True, help="ISO date YYYY-MM-DD")
    research_parser.add_argument("--question", required=True)
    research_parser.add_argument("--intent", default="general_research")
    research_parser.add_argument("--query", default="")
    # Step 04 query subcommands
    for name, help_text, builder, _ in _TREND_QUERY_SPECS:
        builder(sub, name, help_text)
    _build_forum_evidence_pack_parser(sub)
    _build_discussion_topic_evidence_parser(sub)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging()

    command = args.command or "stdio"
    if command == "stdio":
        build_mcp_server(host=args.host, port=args.port, sse_path=args.path).run(transport=args.transport)
    elif command == "create-noop-job":
        print(create_noop_job())
    elif command == "create-sync-thread-job":
        print(dump_json(archive_thread_job(html_path=args.html_path, tid=args.tid, url=args.url, base_url=args.base_url, forum_id=args.forum_id)))
    elif command == "create-thread-archive-job":
        print(
            dump_json(
                create_thread_archive_job(
                    html_path=args.html_path,
                    tid=args.tid,
                    url=args.url,
                    base_url=args.base_url,
                    forum_id=args.forum_id,
                )
            )
        )
    elif command == "inspect-remote-thread":
        print(
            dump_json(
                inspect_remote_thread(
                    tid=args.tid,
                    forum_id=args.forum_id,
                    base_url=args.base_url,
                )
            )
        )
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
    elif command == "refresh-forum-remote-observations":
        print(
            dump_json(
                refresh_forum_remote_observations(
                    forum_id=args.forum_id,
                    page_start=args.page_start,
                    page_end=args.page_end,
                    order=args.order,
                    base_url=args.base_url,
                    cookie_file=args.cookie_file,
                    include_sticky=args.include_sticky,
                    include_announcements=args.include_announcements,
                    dry_run=args.dry_run,
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
    elif command == "preview-thread-context":
        print(dump_json(preview_thread_context(tid=args.tid, context_format_version=args.context_format_version).data or {}))
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
    elif command == "probe-archived-threads":
        print(dump_json(probe_archived_threads(tids=args.tids)))
    elif command == "ensure-thread-archived":
        print(
            dump_json(
                ensure_thread_archived(
                    tid=args.tid,
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
    elif command == "plan-thread-resync-batch":
        result = plan_thread_resync_batch(
            forum_id=args.forum_id,
            pages=args.pages,
            tids=args.tids,
            order=args.order,
            mode=args.mode,
            force=args.force,
            include_unknown=args.include_unknown,
            max_detail_jobs=args.max_detail_jobs,
            persist_observation=args.persist_observation,
        )
        print(dump_json(result.data if hasattr(result, "data") and result.data is not None else {}))
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
    elif command == "list-exports":
        print(dump_json(list_exports()))
    elif command == "read-resource":
        print(dump_json(read_resource(args.uri)))
    elif command == "cleanup-job":
        print(dump_json(cleanup_job(job_id=args.job_id, mode=args.mode, older_than_hours=args.older_than_hours)))
    elif command == "cleanup-orphan-thread-dirs":
        print(dump_json(cleanup_orphan_threads(dry_run=args.dry_run)))
    elif command == "backfill-thread-metadata":
        print(dump_json(backfill_thread_metadata(overwrite_last_reply=args.overwrite_last_reply)))
    elif command == "job-status":
        print(dump_json(get_job_status_payload(args.job_id)))
    elif command == "read-job-events":
        print(dump_json(read_job_events(job_id=args.job_id)))
    elif command == "wait-for-job":
        print(
            dump_json(
                wait_for_job(
                    job_id=args.job_id,
                    timeout_seconds=args.timeout_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    include_events=args.include_events,
                )
            )
        )
    elif command == "check-proxy-pool":
        print(dump_json(check_proxy_pool_health(load_settings())))
    elif command == "create-discussion-trend-index-job":
        thresholds = None
        if args.thresholds_json:
            try:
                thresholds = json.loads(args.thresholds_json)
            except json.JSONDecodeError as exc:
                print(dump_json({"ok": False, "error": {"code": "DISCUSSION_TREND_INVALID_PAYLOAD", "message": f"--thresholds-json is not valid JSON: {exc}"}}))
                return
            if not isinstance(thresholds, dict):
                print(dump_json({"ok": False, "error": {"code": "DISCUSSION_TREND_INVALID_PAYLOAD", "message": "--thresholds-json must decode to an object"}}))
                return
        result = _create_discussion_trend_index_job(
            forum_id=args.forum_id,
            start_date=args.start_date,
            end_date=args.end_date,
            version=args.version,
            thresholds=thresholds,
            retention_success_runs=args.retention_success_runs,
        )
        print(dump_json(to_wire(result)))
    elif command == "create-discussion-trend-report-job":
        result = _create_discussion_trend_report_job(
            forum_id=args.forum_id,
            start_date=args.start_date,
            end_date=args.end_date,
            version=args.version,
            period=args.period,
        )
        print(dump_json(to_wire(result)))
    elif command == "create-forum-research-report-job":
        result = _create_forum_research_report_job(
            forum_id=args.forum_id,
            start_date=args.start_date,
            end_date=args.end_date,
            question=args.question,
            intent=args.intent,
            query=args.query,
        )
        print(dump_json(to_wire(result)))
    elif command in _TREND_QUERY_RUNNERS:
        print(dump_json(to_wire(_TREND_QUERY_RUNNERS[command](args))))
    else:
        parser.print_help()
