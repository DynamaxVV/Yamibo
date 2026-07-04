from __future__ import annotations

import re
import urllib.request
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import Response

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.forums import ForumsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.errors import (
    LoginRequiredError,
    RemoteFetchError,
    RemoteMaintenanceError,
    ThreadPermissionRequiredError,
    UnexpectedPageError,
)
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, has_configured_account_pool
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.forum_list import ForumThreadItem, extract_total_pages
from yamibo_mcp.yamibo.parsers.thread_detail import parse_thread_snapshot
from yamibo_mcp.yamibo.urls import thread_url_from_tid, thread_page_url_from_tid

router = APIRouter(prefix="/api", tags=["remote_forum"])

_THREAD_URL_RE = re.compile(
    r'(https?://bbs\.yamibo\.com/(?:forum\.php\?mod=viewthread&tid=(\d+)[^"\'>\s]*|thread-(\d+)-[\d-]+\.html))',
    re.IGNORECASE,
)


def _open_web_client(settings):
    if has_configured_account_pool(settings):
        return borrow_yamibo_client(settings, prefer_high_permission=True, proxy_url=None)
    resolved_cookie_file = str(settings.cookie_file)
    if not settings.cookie_file.exists():
        fallback = settings.data_dir / "cookies.txt"
        if fallback.exists():
            resolved_cookie_file = str(fallback)
    client = YamiboClient(
        timeout=getattr(settings, "request_timeout_seconds", 15.0),
        cookie_file=resolved_cookie_file,
        use_system_proxy=False,
        proxy_url=None,
        login_username=settings.login_username,
        login_password=settings.login_password,
        request_interval=settings.request_interval_seconds,
        request_interval_jitter=settings.request_interval_jitter_seconds,
    )

    @contextmanager
    def _cm():
        yield None, client
    return _cm()


def _rewrite_thread_links(html: str) -> str:
    def _replace(m: re.Match) -> str:
        tid = m.group(2) or m.group(3)
        if tid:
            return f'/forum/{tid}'
        return m.group(0)
    return _THREAD_URL_RE.sub(_replace, html)


def _clean_rich_body(html: str | None) -> str | None:
    if not html:
        return None
    cleaned = re.sub(
        r'<p><strong>[^<]*</strong>\s*<em>\([\d.]+\s*[KMGT]?B,\s*下载次数:\s*\d+\)</em></p>',
        '', html, flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'<a\b[^>]*>\s*下载附件\s*</a>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(?:&nbsp;)?\s*保存到相册\s*', '', cleaned)
    cleaned = re.sub(r'<p>\s*\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+上传\s*</p>', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip() or None


def _map_remote_error(exc: Exception) -> tuple[int, str, str]:
    if isinstance(exc, LoginRequiredError):
        return 401, "REMOTE_LOGIN_REQUIRED", str(exc)
    if isinstance(exc, ThreadPermissionRequiredError):
        return 403, "THREAD_PERMISSION_REQUIRED", str(exc)
    if isinstance(exc, RemoteMaintenanceError):
        return 503, "REMOTE_MAINTENANCE", str(exc)
    if isinstance(exc, RemoteFetchError):
        return 502, "REMOTE_FETCH_FAILED", str(exc)
    if isinstance(exc, UnexpectedPageError):
        return 502, "UNEXPECTED_REMOTE_PAGE", str(exc)
    if isinstance(exc, ValueError):
        return 400, "INVALID_ARGUMENT", str(exc)
    return 500, "INTERNAL_ERROR", str(exc)


def _build_local_thread_dict(row) -> dict | None:
    if row is None:
        return None
    return {
        "archived": True,
        "sync_time": row["sync_time"] if "sync_time" in row.keys() else None,
        "series_id": row["series_id"] if "series_id" in row.keys() else None,
        "export_path": row["export_path"] if "export_path" in row.keys() else None,
        "content_kind": row["content_kind"] if "content_kind" in row.keys() else None,
    }


def _enrich_items(items: list[dict], conn) -> list[dict]:
    repo = ThreadsRepository(conn)
    for item in items:
        row = repo.get_thread(item["tid"])
        item["archive_status"] = row["archive_status"] if row else None
        item["local_thread"] = _build_local_thread_dict(row)
    return items


def _forum_thread_item_to_dict(item: ForumThreadItem, base_url: str) -> dict:
    return {
        "tid": item.tid, "title": item.title, "display_title": item.title,
        "category": item.category, "row_kind": item.row_kind,
        "publisher": item.publisher, "posted_at": item.posted_at,
        "last_reply_at": item.last_reply_at, "reply_count": item.reply_count,
        "url": thread_url_from_tid(item.tid, base_url=base_url),
    }


@router.get("/remote/forums")
def remote_forums_list(conn: DatabaseConnection = Depends(get_conn)):
    forums_repo = ForumsRepository(conn)
    rows = forums_repo.list_forums()
    return [
        {
            "forum_id": r["forum_id"], "name": r["name"],
            "name_en": r["name_en"] if "name_en" in r.keys() else None,
            "content_kind": r["content_kind"], "enabled": bool(r["enabled"]),
        }
        for r in rows
    ]


@router.get("/remote/forum")
def remote_forum_browse(
    forum_id: int = Query(default=30),
    page: int = Query(default=1, ge=1),
    order: str = Query(default="default"),
    base_url: str = Query(default="https://bbs.yamibo.com"),
    conn: DatabaseConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    forums_repo = ForumsRepository(conn)
    forum = forums_repo.get_forum(forum_id)
    if forum is None or not bool(forum["enabled"]):
        raise HTTPException(status_code=404, detail=f"forum {forum_id} not found or disabled")

    try:
        with _open_web_client(settings) as (_identity, client):
            if order == "dateline":
                result, items, total_pages = client.fetch_forum_threads_dateline(
                    page=page, base_url=base_url, forum_id=forum_id,
                )
            else:
                result, items = client.fetch_forum_threads(
                    page=page, base_url=base_url, forum_id=forum_id,
                )
                total_pages = extract_total_pages(result.html)
    except Exception as exc:
        status, code, message = _map_remote_error(exc)
        raise HTTPException(status_code=status, detail={"error": message, "code": code})

    item_dicts = [
        _forum_thread_item_to_dict(item, base_url)
        for item in items
        if not item.is_sticky and (item.category or "").strip() != "公告"
    ]
    _enrich_items(item_dicts, conn)

    return {
        "source": "remote", "forum_id": forum_id, "page": page, "order": order,
        "total_pages": total_pages, "final_url": result.final_url,
        "items": item_dicts,
    }


@router.get("/remote/threads/{tid}")
def remote_thread_detail(
    tid: int,
    page: int = Query(default=1, ge=1),
    base_url: str = Query(default="https://bbs.yamibo.com"),
    forum_id: int | None = Query(default=None),
    conn: DatabaseConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    try:
        with _open_web_client(settings) as (_identity, client):
            result = client.fetch_thread_page(tid=tid, page=page, base_url=base_url)
    except Exception as exc:
        status, code, message = _map_remote_error(exc)
        raise HTTPException(status_code=status, detail={"error": message, "code": code})

    try:
        snapshot = parse_thread_snapshot(result.html, url=result.final_url, tid=tid)
    except Exception as exc:
        status, code, message = _map_remote_error(exc)
        raise HTTPException(status_code=status, detail={"error": message, "code": code})

    repo = ThreadsRepository(conn)
    local_row = repo.get_thread(tid)

    resolved_forum_id = forum_id
    if resolved_forum_id is None and local_row and local_row["forum_id"] is not None:
        resolved_forum_id = int(local_row["forum_id"])

    content_kind = None
    if local_row and "content_kind" in local_row.keys():
        content_kind = local_row["content_kind"]

    floors = []
    for floor in snapshot.floors:
        image_slots = [
            {"remote_url": url, "local_path": None, "status": "remote"}
            for url in (floor.image_urls or [])
        ]
        floors.append({
            "pid": floor.pid, "floor_no": floor.floor_no,
            "publisher": floor.publisher, "publisher_uid": floor.publisher_uid,
            "content": floor.content or "", "pub_time": floor.pub_time,
            "has_images": floor.has_images,
            "quote_text": floor.quote_text, "reply_text": floor.reply_text,
            "rich_body_html": _clean_rich_body(_rewrite_thread_links(floor.rich_body_html)) if floor.rich_body_html else None,
            "image_slots": image_slots,
        })

    return {
        "source": "remote", "tid": tid, "forum_id": resolved_forum_id,
        "page": page, "total_pages": None,
        "url": thread_page_url_from_tid(tid, page=page, base_url=base_url),
        "raw_title": snapshot.raw_title, "display_title": snapshot.display_title,
        "publisher": snapshot.publisher, "publisher_uid": snapshot.publisher_uid,
        "pub_time": snapshot.pub_time, "content_kind": content_kind,
        "archive_status": local_row["archive_status"] if local_row else None,
        "local_thread": _build_local_thread_dict(local_row),
        "floors": floors,
    }


@router.get("/remote/image")
def remote_image_proxy(
    url: str = Query(default=""),
    settings: Settings = Depends(get_settings),
):
    if not url:
        raise HTTPException(status_code=400, detail="url required")

    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc or "yamibo.com" not in parsed.netloc:
        raise HTTPException(status_code=400, detail="only yamibo URLs are supported")

    try:
        with _open_web_client(settings) as (_identity, client):
            resp = client._session.get(url, headers=client._request_headers(referer="https://bbs.yamibo.com/"), timeout=client.timeout)
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            body = resp.content
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to fetch image: {exc}")

    return Response(content=body, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})
