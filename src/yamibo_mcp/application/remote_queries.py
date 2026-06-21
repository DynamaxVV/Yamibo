from __future__ import annotations

import re

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.server.schemas import build_series_summary, thread_summary_payload
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.forum_list import ForumThreadItem
from yamibo_mcp.yamibo.parsers.search_results import SearchResultItem
from yamibo_mcp.yamibo.title.normalizer import normalize_series_key
from yamibo_mcp.yamibo.urls import thread_url_from_tid
from yamibo_mcp.application.remote_inspection import inspect_remote_thread


def browse_forum_page(
    *,
    page: int,
    forum_id: int = 30,
    order: str = "default",
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> dict[str, object]:
    if page <= 0:
        raise ValueError("page must be positive")

    settings = load_settings()
    client = YamiboClient(
        cookie_file=cookie_file or str(settings.cookie_file),
        use_system_proxy=settings.use_system_proxy,
        login_username=settings.login_username,
        login_password=settings.login_password,
        request_interval=settings.request_interval_seconds,
        request_interval_jitter=settings.request_interval_jitter_seconds,
    )
    if order == "dateline":
        result, items, total_pages = client.fetch_forum_threads_dateline(page=page, base_url=base_url, forum_id=forum_id)
    else:
        result, items = client.fetch_forum_threads(page=page, base_url=base_url, forum_id=forum_id)
        total_pages = 0

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
            "order": order,
            "total_pages": total_pages,
            "forum_url": result.final_url,
            "base_url": base_url,
            "include_sticky": include_sticky,
            "include_announcements": include_announcements,
            "count": len(filtered),
            "items": filtered,
        }
    finally:
        conn.close()


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
    if posted_on:
        start_page = 1
        end_page = None
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
        except Exception as exc:  # noqa: BLE001 - remote search falls back to local FTS
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


def _normalize_date_only(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value.strip())
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _matches_remote_filters(item: ForumThreadItem | SearchResultItem, *, query: str, posted_on: str | None) -> bool:
    haystack = " ".join([item.title, item.category or "", item.publisher or ""])
    normalized_haystack = normalize_series_key(haystack)
    normalized_query = normalize_series_key(query) if query else ""
    if normalized_query and normalized_query not in normalized_haystack:
        return False
    if posted_on and _normalize_date_only(item.posted_at) != _normalize_date_only(posted_on):
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
) -> tuple[list[SearchResultItem | ForumThreadItem], list[str], int]:
    settings = load_settings()
    client = YamiboClient(
        cookie_file=cookie_file or str(settings.cookie_file),
        use_system_proxy=settings.use_system_proxy,
        login_username=settings.login_username,
        login_password=settings.login_password,
        request_interval=settings.request_interval_seconds,
        request_interval_jitter=settings.request_interval_jitter_seconds,
    )

    if posted_on:
        return _dateline_search(client, query=query, limit=limit, posted_on=posted_on, base_url=base_url, forum_id=forum_id)

    items, scanned_pages, total_pages = client.fetch_search_results_all(
        query=query,
        base_url=base_url,
        forum_id=forum_id,
        start_page=start_page,
        end_page=end_page,
    )
    filtered = [item for item in items if _matches_remote_filters(item, query=query, posted_on=None)]
    if limit > 0:
        filtered = filtered[:limit]
    return filtered, scanned_pages, total_pages


def _dateline_search(
    client: YamiboClient,
    *,
    query: str,
    limit: int,
    posted_on: str,
    base_url: str,
    forum_id: int,
) -> tuple[list[ForumThreadItem], list[str], int]:
    normalized_query = normalize_series_key(query) if query else ""
    normalized_date = _normalize_date_only(posted_on)
    scanned_pages: list[str] = []
    collected: list[ForumThreadItem] = []
    step = 1
    page = 1
    prev_page = 0
    found_boundary = False

    def _page_info(result, items):
        non_sticky = [it for it in items if not it.is_sticky and (it.category or "").strip() != "公告"]
        oldest = None
        newest = None
        for it in non_sticky:
            d = _normalize_date_only(it.posted_at)
            if d:
                oldest = d if oldest is None or d < oldest else oldest
                newest = d if newest is None or d > newest else newest
        has_target = any(_normalize_date_only(it.posted_at) == normalized_date for it in non_sticky)
        return non_sticky, oldest, newest, has_target

    def _collect(non_sticky):
        for it in non_sticky:
            if _normalize_date_only(it.posted_at) != normalized_date:
                continue
            if normalized_query:
                haystack = normalize_series_key(f"{it.title} {it.category or ''} {it.publisher or ''}")
                if normalized_query not in haystack:
                    continue
            collected.append(it)
            if limit > 0 and len(collected) >= limit:
                return True
        return False

    first_result, first_items, total_pages = client.fetch_forum_threads_dateline(page=1, base_url=base_url, forum_id=forum_id)
    scanned_pages.append(first_result.final_url)
    growth_factor = max(2, min(5, (total_pages // 100) + 1))

    non_sticky, oldest, newest, has_target = _page_info(first_result, first_items)
    if oldest and oldest >= normalized_date and newest and newest >= normalized_date:
        if _collect(non_sticky):
            return collected, scanned_pages, total_pages
    elif oldest and oldest < normalized_date:
        found_boundary = True
        lo_page, hi_page = 0, 1
        _collect(non_sticky)

    if not found_boundary:
        page = 1 + step
        prev_page = 1
        while page <= total_pages:
            result, items, _ = client.fetch_forum_threads_dateline(page=page, base_url=base_url, forum_id=forum_id)
            scanned_pages.append(result.final_url)
            non_sticky, oldest, newest, has_target = _page_info(result, items)
            if not non_sticky:
                if page >= total_pages:
                    break
                prev_page = page
                page = min(page + step, total_pages)
                step *= growth_factor
                continue
            if has_target and oldest and oldest < normalized_date:
                _collect(non_sticky)
                found_boundary = True
                break
            if oldest and oldest < normalized_date:
                found_boundary = True
                lo_page, hi_page = prev_page, page
                _collect(non_sticky)
                break
            if has_target and oldest and oldest >= normalized_date:
                if _collect(non_sticky):
                    return collected, scanned_pages, total_pages
                prev_page = page
                page = min(page + step, total_pages)
                step *= growth_factor
                continue
            prev_page = page
            page = min(page + step, total_pages)
            step *= growth_factor

    if found_boundary and lo_page + 1 < hi_page:
        while lo_page + 1 < hi_page:
            mid = (lo_page + hi_page) // 2
            result, items, _ = client.fetch_forum_threads_dateline(page=mid, base_url=base_url, forum_id=forum_id)
            scanned_pages.append(result.final_url)
            non_sticky, oldest, _, has_target = _page_info(result, items)
            if not non_sticky:
                lo_page = mid
                continue
            if has_target:
                _collect(non_sticky)
            if oldest and oldest < normalized_date:
                hi_page = mid
            else:
                lo_page = mid

        for scan_page in range(max(1, hi_page - 1), hi_page + 2):
            if scan_page > total_pages:
                break
            if scan_page == hi_page:
                continue
            result, items, _ = client.fetch_forum_threads_dateline(page=scan_page, base_url=base_url, forum_id=forum_id)
            scanned_pages.append(result.final_url)
            non_sticky, _, newest, has_target = _page_info(result, items)
            if has_target:
                _collect(non_sticky)
            if newest and newest < normalized_date:
                break

    if collected:
        seen: set[int] = set()
        deduped: list[ForumThreadItem] = []
        for it in collected:
            if it.tid not in seen:
                seen.add(it.tid)
                deduped.append(it)
        collected = deduped
    return collected, scanned_pages, total_pages


def _local_row_by_tid(repo: ThreadsRepository, tid: int):
    row = repo.get_thread(tid)
    if row is None:
        return None, None
    return row, repo.get_title_parse(tid)


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
            payload["series"] = build_series_summary(
                int(thread_row["series_id"]),
                canonical_title=title_row["core_title_guess"] if title_row else None,
            )
    return payload
