from __future__ import annotations

import json
from pathlib import Path
import re

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.resources import (
    build_resource_payload,
    guess_content_type,
    parse_resource_uri,
    series_chapters_uri,
    series_index_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_assets_uri,
)
from yamibo_mcp.server.schemas import thread_summary_payload
from yamibo_mcp.server.schemas import build_series_summary
from yamibo_mcp.services.llm_client import openai_compatible_chat
from yamibo_mcp.services.title_llm import refine_title_parse_with_llm
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.forum_list import ForumThreadItem
from yamibo_mcp.yamibo.parsers.search_results import SearchResultItem
from yamibo_mcp.yamibo.title.parser import parse_title
from yamibo_mcp.yamibo.title.normalizer import normalize_series_key
from yamibo_mcp.yamibo.urls import forum_page_url, thread_url_from_tid
from yamibo_mcp.application.thread_use_cases import ensure_thread, archive_thread_job
from yamibo_mcp.application.job_use_cases import get_job_status_payload as _get_job_status_payload


def create_noop_job() -> str:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        job = repo.create(JobType.NOOP.value)
        return job.job_id
    finally:
        conn.close()


def archive_thread(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, object]:
    return archive_thread_job(html_path=html_path, tid=tid, url=url, base_url=base_url, forum_id=forum_id)


def sync_thread(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, object]:
    # 向后兼容旧调用；正式 MCP 暴露名称改为 archive_thread。
    return archive_thread(html_path=html_path, tid=tid, url=url, base_url=base_url, forum_id=forum_id)


def export_thread(*, tid: int, strategy: str | None = None) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        payload = {"tid": tid}
        if strategy:
            payload["strategy"] = strategy
        job = repo.create(JobType.EXPORT_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id}
    finally:
        conn.close()


def cleanup_job(*, job_id: str | None = None, mode: str = "job_staging", older_than_hours: int | None = None) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        payload = {"mode": mode}
        if job_id is not None:
            payload["job_id"] = job_id
        if older_than_hours is not None:
            payload["older_than_hours"] = older_than_hours
        job = repo.create(JobType.CLEANUP_JOB.value, payload=payload)
        return {"job_id": job.job_id}
    finally:
        conn.close()


def sync_forum_range(
    *,
    start_page: int,
    end_page: int,
    forum_id: int = 30,
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> dict[str, object]:
    if start_page <= 0 or end_page <= 0 or end_page < start_page:
        raise ValueError("invalid forum page range")

    settings = load_settings()
    resolved_cookie_file = cookie_file or str(settings.cookie_file)
    client = YamiboClient(
        cookie_file=resolved_cookie_file,
        use_system_proxy=settings.use_system_proxy,
        login_username=settings.login_username,
        login_password=settings.login_password,
    )
    collected: list[ForumThreadItem] = []
    scanned_pages: list[str] = []
    seen_tids: set[int] = set()

    for page in range(start_page, end_page + 1):
        result, items = client.fetch_forum_threads(page=page, base_url=base_url, forum_id=forum_id)
        scanned_pages.append(result.final_url)
        for item in items:
            if not include_sticky and item.is_sticky:
                continue
            if not include_announcements and (item.category or "").strip() == "公告":
                continue
            if item.tid in seen_tids:
                continue
            seen_tids.add(item.tid)
            collected.append(item)

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        job_items: list[dict[str, object]] = []
        for item in collected:
            thread_url = thread_url_from_tid(item.tid, base_url=base_url)
            job = repo.create(
                JobType.SYNC_THREAD.value,
                tid=item.tid,
                payload={"tid": item.tid, "url": thread_url, "base_url": base_url, "forum_id": forum_id},
            )
            job_items.append(
                {
                    "job_id": job.job_id,
                    "tid": item.tid,
                    "title": item.title,
                    "url": thread_url,
                    "category": item.category,
                }
            )
        return {
            "start_page": start_page,
            "end_page": end_page,
            "include_sticky": include_sticky,
            "include_announcements": include_announcements,
            "scanned_pages": scanned_pages,
            "count": len(job_items),
            "items": job_items,
        }
    finally:
        conn.close()


def browse_forum_page(
    *,
    page: int,
    forum_id: int = 30,
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> dict[str, object]:
    if page <= 0:
        raise ValueError("page must be positive")

    settings = load_settings()
    resolved_cookie_file = cookie_file or str(settings.cookie_file)
    client = YamiboClient(
        cookie_file=resolved_cookie_file,
        use_system_proxy=settings.use_system_proxy,
        login_username=settings.login_username,
        login_password=settings.login_password,
    )
    result, items = client.fetch_forum_threads(page=page, base_url=base_url, forum_id=forum_id)

    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        filtered: list[dict[str, object]] = []
        for item in items:
            if not include_sticky and item.is_sticky:
                continue
            if not include_announcements and (item.category or "").strip() == "公告":
                continue
            thread_row, title_row = _local_row_by_tid(repo, item.tid)
            payload = _search_item_from_remote(item, thread_row, title_row, base_url=base_url)
            payload["page"] = page
            payload["forum_url"] = result.final_url
            filtered.append(payload)
        return {
            "source": "forum_page",
            "page": page,
            "forum_id": forum_id,
            "forum_url": result.final_url,
            "base_url": base_url,
            "include_sticky": include_sticky,
            "include_announcements": include_announcements,
            "count": len(filtered),
            "items": filtered,
        }
    finally:
        conn.close()


def get_job_status(job_id: str) -> dict[str, object]:
    return _get_job_status_payload(job_id)


def _normalize_date_only(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value.strip())
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _matches_remote_filters(item: ForumThreadItem | SearchResultItem, *, query: str, posted_on: str | None) -> bool:
    haystack = " ".join(
        [
            item.title,
            item.category or "",
            item.publisher or "",
        ]
    )
    normalized_haystack = normalize_series_key(haystack)
    normalized_query = normalize_series_key(query) if query else ""
    if normalized_query and normalized_query not in normalized_haystack:
        return False
    if posted_on:
        if _normalize_date_only(item.posted_at) != _normalize_date_only(posted_on):
            return False
    return True


def _remote_search_items(
    *,
    query: str,
    limit: int,
    start_page: int,
    end_page: int | None,
    posted_on: str | None,
    base_url: str,
    cookie_file: str | None,
    forum_id: int = 30,
) -> tuple[list[SearchResultItem], list[str], int]:
    settings = load_settings()
    client = YamiboClient(
        cookie_file=cookie_file or str(settings.cookie_file),
        use_system_proxy=settings.use_system_proxy,
        login_username=settings.login_username,
        login_password=settings.login_password,
    )
    items, scanned_pages, total_pages = client.fetch_search_results_all(
        query=query,
        base_url=base_url,
        forum_id=forum_id,
        start_page=start_page,
        end_page=end_page,
    )
    filtered = [item for item in items if _matches_remote_filters(item, query=query, posted_on=posted_on)]
    if limit > 0:
        filtered = filtered[:limit]
    return filtered, scanned_pages, total_pages


def _local_row_by_tid(repo: ThreadsRepository, tid: int):
    row = repo.get_thread(tid)
    if row is None:
        return None, None
    title = repo.get_title_parse(tid)
    return row, title


def _search_item_from_remote(item: ForumThreadItem | SearchResultItem, thread_row, title_row, *, base_url: str) -> dict[str, object]:
    payload = {
        "tid": item.tid,
        "url": thread_url_from_tid(item.tid, base_url=base_url),
        "display_title": item.title,
        "raw_title": item.title,
        "core_title": None if title_row is None else title_row["core_title_guess"],
        "chapter_name": None if title_row is None else title_row["chapter_name"],
        "series_id": None if thread_row is None else thread_row["series_id"],
        "series_key": None if title_row is None else title_row["series_key"],
        "chapter_title": None if title_row is None else title_row["chapter_title"],
        "archive_status": None if thread_row is None else thread_row["archive_status"],
        "validation_status": None if thread_row is None else thread_row["validation_status"],
        "sync_time": None if thread_row is None else thread_row["sync_time"],
        "export_path": None if thread_row is None else thread_row["export_path"],
        "category": item.category,
        "publisher": item.publisher,
        "posted_at": item.posted_at,
        "last_reply_at": getattr(item, "last_reply_at", None),
        "reply_count": item.reply_count,
        "row_kind": getattr(item, "row_kind", "search_result"),
        "excerpt": getattr(item, "excerpt", None),
        "resources": None,
    }
    if thread_row is not None:
        payload["resources"] = thread_summary_payload(thread_row, include_export=True)["resources"]
        if thread_row["series_id"] is not None:
            payload["series"] = build_series_summary(int(thread_row["series_id"]), canonical_title=title_row["core_title_guess"] if title_row else None)
    return payload


def search_threads(
    *,
    query: str = "",
    forum_id: int = 30,
    limit: int = 0,
    start_page: int = 1,
    end_page: int | None = None,
    posted_on: str | None = None,
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> dict[str, object]:
    if start_page <= 0 or (end_page is not None and end_page <= 0) or (end_page is not None and end_page < start_page):
        raise ValueError("invalid forum page range")
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        remote_error: dict[str, str] | None = None
        result_source = "forum"
        total_result_pages = 0
        try:
            remote_items, scanned_pages, total_result_pages = _remote_search_items(
                query=query.strip(),
                limit=limit,
                start_page=start_page,
                end_page=end_page,
                posted_on=posted_on,
                base_url=base_url,
                cookie_file=cookie_file,
                forum_id=forum_id,
            )
        except Exception as exc:  # noqa: BLE001 - 远端不可用时保留本地检索能力
            remote_items = []
            scanned_pages = []
            total_result_pages = 0
            remote_error = {"code": exc.__class__.__name__, "message": str(exc)}
            result_source = "local_fallback"
        merged: list[dict[str, object]] = []

        for item in remote_items:
            thread_row, title_row = _local_row_by_tid(repo, item.tid)
            merged.append(_search_item_from_remote(item, thread_row, title_row, base_url=base_url))
        if remote_error is not None and query.strip():
            local_rows = repo.search_threads(query, limit=max(limit, 200) if limit > 0 else 500)
            for row in local_rows:
                payload = thread_summary_payload(row, include_export=True)
                payload.update(
                    {
                        "category": None,
                        "publisher": row["publisher"] if "publisher" in row.keys() else None,
                        "posted_at": row["sync_time"] if "sync_time" in row.keys() else None,
                        "last_reply_at": None,
                        "reply_count": None,
                        "row_kind": "archived",
                    }
                )
                if payload.get("series_id") is not None:
                    payload["series"] = build_series_summary(int(payload["series_id"]), canonical_title=payload.get("core_title"))
                merged.append(payload)

        resolved_end_page = end_page if end_page is not None else total_result_pages
        if resolved_end_page == 0:
            resolved_end_page = start_page

        return {
            "query": query,
            "source": result_source,
            "forum_id": forum_id,
            "posted_on": posted_on,
            "start_page": start_page,
            "end_page": resolved_end_page,
            "total_result_pages": total_result_pages,
            "limit": limit,
            "scanned_pages": scanned_pages,
            "remote_error": remote_error,
            "count": len(merged),
            "items": merged,
        }
    finally:
        conn.close()


def get_thread(*, tid: int, url: str | None = None, base_url: str | None = None, forum_id: int | None = None) -> dict[str, object]:
    return ensure_thread(tid=tid, url=url, base_url=base_url, forum_id=forum_id)


def list_exports(*, limit: int = 100) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        rows = ThreadsRepository(conn).list_exports(limit=limit)
        return {
            "count": len(rows),
            "items": [thread_summary_payload(row, include_export=True) for row in rows],
        }
    finally:
        conn.close()


def read_resource(uri: str) -> dict[str, object]:
    settings = load_settings()
    kind_root, tid, kind = parse_resource_uri(uri)
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir)
    if kind_root == "threads":
        assert tid is not None
        if kind == "context":
            path = paths.thread_context(tid)
        elif kind == "metadata":
            path = paths.thread_metadata(tid)
        elif kind == "export":
            conn = connect(settings.db_path)
            try:
                migrate(conn)
                row = ThreadsRepository(conn).get_thread(tid)
            finally:
                conn.close()
            export_value = None if row is None else row["export_path"]
            if not export_value:
                path = paths.thread_export_zip(tid)
            else:
                export_path = Path(str(export_value))
                path = export_path if export_path.is_absolute() else settings.data_dir / export_path
        elif kind == "summary":
            return _build_thread_summary_resource(uri, tid, settings)
        elif kind == "diagnostics":
            return _build_thread_diagnostics_resource(uri, tid, settings)
        elif kind == "posts":
            return _build_thread_posts_resource(uri, tid, settings)
        elif kind == "assets":
            return _build_thread_assets_resource(uri, tid, settings)
        else:
            raise ValueError(f"unsupported resource kind: {kind}")
        payload = build_resource_payload(uri=uri, path=path, content_type=guess_content_type(kind))
        if payload["exists"]:
            if kind == "export":
                payload["size_bytes"] = path.stat().st_size
            else:
                # 文本资源直接内联返回，后续切 MCP Resources 时可以保留同一读取语义。
                payload["text"] = path.read_text(encoding="utf-8")
        return payload
    if kind_root == "series" and kind == "index":
        path = paths.series_index()
        text = generate_series_index_markdown(write_path=path)
        payload = build_resource_payload(uri=uri, path=path, content_type=guess_content_type(kind))
        payload["exists"] = True
        payload["text"] = text
        return payload
    if kind_root == "series" and kind == "chapters":
        assert tid is not None
        path = paths.series_chapters(tid)
        text = generate_series_chapters_json(series_id=tid, write_path=path)
        payload = build_resource_payload(uri=uri, path=path, content_type=guess_content_type(kind))
        payload["exists"] = True
        payload["text"] = text
        return payload
    if kind_root == "forums" and kind == "index":
        return _build_forums_index_resource(uri, settings)
    if kind_root == "forums" and kind == "summary":
        assert tid is not None
        return _build_forum_summary_resource(uri, tid, settings)
    if kind_root == "jobs" and kind.endswith("/events"):
        job_id = kind.split("/")[0]
        return _build_job_events_resource(uri, job_id, settings)
    raise ValueError(f"unsupported resource root: {kind_root}")


def read_resource_content(uri: str) -> tuple[str | bytes, str]:
    payload = read_resource(uri)
    content_type = str(payload["content_type"])
    if not payload.get("exists"):
        raise FileNotFoundError(f"resource does not exist: {uri}")
    if content_type == "application/zip":
        path = Path(str(payload["path"]))
        return path.read_bytes(), content_type
    return str(payload.get("text") or ""), content_type


def _build_forums_index_resource(uri: str, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        rows = conn.execute("SELECT * FROM forums ORDER BY forum_id").fetchall()
    finally:
        conn.close()
    forums = [
        {
            "forum_id": row["forum_id"],
            "name": row["name"],
            "content_kind": row["content_kind"],
            "base_url": row["base_url"],
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]
    return {
        "uri": uri,
        "content_type": "application/json",
        "exists": True,
        "text": json.dumps(forums, ensure_ascii=False, indent=2),
    }


def _build_forum_summary_resource(uri: str, forum_id: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        row = conn.execute("SELECT * FROM forums WHERE forum_id = ?", (forum_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {
            "uri": uri,
            "content_type": "application/json",
            "exists": False,
            "error": f"forum {forum_id} not found",
        }
    summary = {
        "forum_id": row["forum_id"],
        "name": row["name"],
        "content_kind": row["content_kind"],
        "base_url": row["base_url"],
        "enabled": bool(row["enabled"]),
    }
    return {
        "uri": uri,
        "content_type": "application/json",
        "exists": True,
        "text": json.dumps(summary, ensure_ascii=False, indent=2),
    }


def _build_thread_summary_resource(uri: str, tid: int, settings) -> dict[str, object]:
    """Compact thread summary — does NOT read full context.md."""
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        thread = repo.get_thread(tid)
        if thread is None:
            return {
                "uri": uri,
                "content_type": "application/json",
                "exists": False,
                "error": f"thread {tid} not found",
            }
        title = repo.get_title_parse(tid)
    finally:
        conn.close()
    summary = {
        "tid": thread["tid"],
        "display_title": thread["display_title"] or thread["raw_title"],
        "raw_title": thread["raw_title"],
        "archive_status": thread["archive_status"],
        "validation_status": thread["validation_status"],
        "image_count": thread["image_count"],
        "sync_time": thread["sync_time"],
        "publisher": thread["publisher"],
        "series_id": thread["series_id"],
        "core_title": None if title is None else title["core_title_guess"],
        "series_key": None if title is None else title["series_key"],
        "chapter_name": None if title is None else title["chapter_name"],
        "confidence": None if title is None else title["confidence"],
        "needs_review": None if title is None else bool(title["needs_review"]),
        "resources": {
            "context": thread_context_uri(tid),
            "metadata": thread_metadata_uri(tid),
            "diagnostics": thread_diagnostics_uri(tid),
            "posts": thread_posts_uri(tid),
            "assets": thread_assets_uri(tid),
        },
    }
    return {
        "uri": uri,
        "content_type": "application/json",
        "exists": True,
        "text": json.dumps(summary, ensure_ascii=False, indent=2),
    }


def _build_thread_diagnostics_resource(uri: str, tid: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        thread = repo.get_thread(tid)
        if thread is None:
            return {
                "uri": uri,
                "content_type": "application/json",
                "exists": False,
                "error": f"thread {tid} not found",
            }
        from yamibo_mcp.db.repositories.assets import AssetsRepository
        assets = AssetsRepository(conn).list_assets(tid)
    finally:
        conn.close()
    missing_urls = json.loads(thread["missing_images_json"] or "[]")
    required_assets = [a for a in assets if a["required"]]
    missing_required = [a for a in required_assets if a["status"] == "missing"]
    warnings: list[str] = []
    next_actions: list[str] = []
    archive_status = thread["archive_status"]
    if archive_status == "stale":
        next_actions.append("archive_thread")
    elif archive_status == "partial":
        warnings.append(f"missing {len(missing_urls)} images")
        next_actions.append("re-export after re-sync")
    elif archive_status == "complete" and not thread["export_path"]:
        next_actions.append("export_thread")
    if missing_required:
        warnings.append(f"missing {len(missing_required)} required assets")
    diagnostics = {
        "tid": tid,
        "archive_status": archive_status,
        "validation_status": thread["validation_status"],
        "image_count": thread["image_count"],
        "missing_image_count": len(missing_urls),
        "required_assets_count": len(required_assets),
        "missing_required_assets_count": len(missing_required),
        "is_exported": bool(thread["is_exported"]),
        "export_path": thread["export_path"],
        "warnings": warnings,
        "next_actions": next_actions,
    }
    return {
        "uri": uri,
        "content_type": "application/json",
        "exists": True,
        "text": json.dumps(diagnostics, ensure_ascii=False, indent=2),
    }


def _build_thread_posts_resource(uri: str, tid: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
        thread = ThreadsRepository(conn).get_thread(tid)
        if thread is None:
            conn.close()
            return {
                "uri": uri,
                "content_type": "application/json",
                "exists": False,
                "error": f"thread {tid} not found",
            }
        blocks = ContentBlocksRepository(conn).list_blocks(tid)
    finally:
        conn.close()
    posts_data = [
        {
            "id": block["id"],
            "tid": block["tid"],
            "pid": block["pid"],
            "order_index": block["order_index"],
            "block_type": block["block_type"],
            "text": block["text"],
            "asset_id": block["asset_id"],
            "metadata": json.loads(block["metadata_json"] or "{}"),
        }
        for block in blocks
    ]
    return {
        "uri": uri,
        "content_type": "application/json",
        "exists": True,
        "text": json.dumps(posts_data, ensure_ascii=False, indent=2),
    }


def _build_thread_assets_resource(uri: str, tid: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        from yamibo_mcp.db.repositories.assets import AssetsRepository
        thread = ThreadsRepository(conn).get_thread(tid)
        if thread is None:
            conn.close()
            return {
                "uri": uri,
                "content_type": "application/json",
                "exists": False,
                "error": f"thread {tid} not found",
            }
        assets = AssetsRepository(conn).list_assets(tid)
    finally:
        conn.close()
    assets_data = [
        {
            "asset_id": asset["asset_id"],
            "tid": asset["tid"],
            "pid": asset["pid"],
            "asset_type": asset["asset_type"],
            "remote_url": asset["remote_url"],
            "local_path": asset["local_path"],
            "exportable": bool(asset["exportable"]),
            "required": bool(asset["required"]),
            "status": asset["status"],
        }
        for asset in assets
    ]
    return {
        "uri": uri,
        "content_type": "application/json",
        "exists": True,
        "text": json.dumps(assets_data, ensure_ascii=False, indent=2),
    }


def _build_job_events_resource(uri: str, job_id: str, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        from yamibo_mcp.db.repositories.job_events import JobEventsRepository
        events = JobEventsRepository(conn).list(job_id=job_id)
    finally:
        conn.close()
    events_data = [
        {
            "event_id": event.event_id,
            "job_id": event.job_id,
            "event_type": event.event_type,
            "status": event.status,
            "stage": event.stage,
            "payload": event.payload,
            "created_at": event.created_at,
        }
        for event in events
    ]
    return {
        "uri": uri,
        "content_type": "application/json",
        "exists": True,
        "text": json.dumps(events_data, ensure_ascii=False, indent=2),
    }


def parse_thread_title(*, title: str, use_llm_on_low_confidence: bool = True) -> dict[str, object]:
    settings = load_settings()
    parsed = parse_title(title)
    llm_used = False
    llm_meta = None
    if use_llm_on_low_confidence:
        parsed, llm_meta = refine_title_parse_with_llm(settings, raw_title=title, parsed=parsed)
        llm_used = False if llm_meta is None else bool(llm_meta.get("used"))
    return {
        "display_title": parsed.display_title,
        "group_name": parsed.group_name,
        "author_guess": parsed.author_guess,
        "core_title_guess": parsed.core_title_guess,
        "normalized_core_title": parsed.normalized_core_title,
        "series_key": parsed.series_key,
        "title_aliases": parsed.title_aliases,
        "chapter_name": parsed.chapter_name,
        "chapter_index": parsed.chapter_index,
        "chapter_index_end": parsed.chapter_index_end,
        "chapter_title": parsed.chapter_title,
        "subtitle": parsed.subtitle,
        "tags": parsed.tags,
        "confidence": parsed.confidence,
        "needs_review": parsed.needs_review,
        "llm_used": llm_used,
        "llm_meta": llm_meta if use_llm_on_low_confidence else None,
    }


def llm_transform_text(
    *,
    task: str,
    text: str,
    system_prompt: str | None = None,
    temperature: float = 0.0,
) -> dict[str, object]:
    settings = load_settings()
    prompt = system_prompt or "You extract or clean forum text. Return concise structured plain text unless JSON is explicitly requested."
    result = openai_compatible_chat(
        settings,
        system_prompt=prompt,
        user_prompt=f"Task: {task}\n\nInput:\n{text}",
        temperature=temperature,
    )
    return {"task": task, "model": result["model"], "content": result["content"]}


def dump_json(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def create_sync_thread_job(*, html_path: str, tid: int | None = None, url: str | None = None) -> str:
    return str(archive_thread(html_path=html_path, tid=tid, url=url)["job_id"])


def create_export_thread_job(*, tid: int) -> str:
    return str(export_thread(tid=tid)["job_id"])


def generate_series_index_markdown(*, limit: int = 100, write_path: Path | None = None) -> str:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = SeriesRepository(conn)
        rows = repo.list_series(limit=limit)
        lines = ["# Series Index", ""]
        for row in rows:
            series_id = int(row["series_id"])
            lines.append(f"## {row['canonical_title'] or '(untitled)'}")
            lines.append(f"- series_id: {series_id}")
            lines.append(f"- series_key: {row['series_key'] or ''}")
            lines.append(f"- author: {row['author_guess'] or ''}")
            lines.append(f"- threads: {row['thread_count']}")
            lines.append(f"- chapters_resource_uri: {series_chapters_uri(series_id)}")
            threads = repo.list_threads_for_series(series_id)
            if threads:
                lines.append("- chapters:")
                for thread in threads[:20]:
                    thread_tid = int(thread["tid"])
                    lines.append(
                        f"  - {thread['chapter_name'] or thread['display_title'] or thread['raw_title']} "
                        f"(tid={thread_tid}, context={thread_summary_payload(thread).get('resources', {}).get('context')})"
                    )
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        if write_path is not None:
            write_path.parent.mkdir(parents=True, exist_ok=True)
            write_path.write_text(text, encoding="utf-8")
        return text
    finally:
        conn.close()


def generate_series_chapters_json(*, series_id: int, write_path: Path | None = None) -> str:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = SeriesRepository(conn)
        series = repo.get_series(series_id)
        if series is None:
            raise ValueError(f"series not found: {series_id}")
        threads = repo.list_threads_for_series(series_id)
        payload = {
            "series_id": series_id,
            "canonical_title": series["canonical_title"],
            "series_key": series["series_key"],
            "aliases": json.loads(series["aliases_json"] or "[]"),
            "thread_count": len(threads),
            "chapters": [
                {
                    "tid": int(thread["tid"]),
                    "title": thread["display_title"] or thread["raw_title"],
                    "chapter_name": thread["chapter_name"],
                    "chapter_index": thread["chapter_index"],
                    "chapter_index_end": thread.get("chapter_index_end"),
                    "archive_status": thread["archive_status"],
                    "sync_time": thread["sync_time"],
                    "resources": thread_summary_payload(thread, include_export=True)["resources"],
                }
                for thread in threads
            ],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if write_path is not None:
            write_path.parent.mkdir(parents=True, exist_ok=True)
            write_path.write_text(text, encoding="utf-8")
        return text
    finally:
        conn.close()
