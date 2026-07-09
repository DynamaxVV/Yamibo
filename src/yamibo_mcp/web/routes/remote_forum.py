from __future__ import annotations

import ipaddress
import re
import urllib.parse
import urllib.request
from http import HTTPStatus

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.forums import ForumsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.errors import (
    LoginRequiredError,
    RemoteFetchError,
    RemoteMaintenanceError,
    ThreadPermissionRequiredError,
    UnexpectedPageError,
)
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, has_configured_account_pool
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.forum_list import ForumThreadItem, extract_total_pages
from yamibo_mcp.yamibo.parsers.thread_detail import parse_thread_detail, parse_thread_snapshot
from yamibo_mcp.yamibo.title.parser import parse_title
from yamibo_mcp.yamibo.urls import thread_url_from_tid, thread_page_url_from_tid
from ._helpers import json_response, error_response


def _open_web_client(settings):
    """Create a client for web browsing — highest permission, no proxy."""
    if has_configured_account_pool(settings):
        return borrow_yamibo_client(
            settings, prefer_high_permission=True, proxy_url=None,
        )
    # Fallback: simple client without proxy.
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
    from contextlib import contextmanager
    @contextmanager
    def _cm():
        yield None, client
    return _cm()

# Match yamibo thread URLs to extract tid for internal link rewriting.
_THREAD_URL_RE = re.compile(
    r'(https?://bbs\.yamibo\.com/(?:forum\.php\?mod=viewthread&tid=(\d+)[^"\'>\s]*|thread-(\d+)-[\d-]+\.html))',
    re.IGNORECASE,
)


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
    # Remove attachment metadata: filename + size, download links, upload timestamps.
    cleaned = re.sub(
        r'<p><strong>[^<]*</strong>\s*<em>\([\d.]+\s*[KMGT]?B,\s*下载次数:\s*\d+\)</em></p>',
        '', html, flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r'<a\b[^>]*>\s*下载附件\s*</a>', '', cleaned, flags=re.IGNORECASE,
    )
    cleaned = re.sub(r'(?:&nbsp;)?\s*保存到相册\s*', '', cleaned)
    cleaned = re.sub(
        r'<p>\s*\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+上传\s*</p>', '', cleaned,
    )
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


def _is_allowed_remote_image_url(raw_url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


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


def _forum_thread_item_to_dict(item: ForumThreadItem, base_url: str, *, content_kind: str | None = None) -> dict:
    parsed_title = parse_title(item.title)
    return {
        "tid": item.tid,
        "title": item.title,
        "display_title": item.title,
        "category": item.category,
        "row_kind": item.row_kind,
        "publisher": item.publisher,
        "posted_at": item.posted_at,
        "last_reply_at": item.last_reply_at,
        "reply_count": item.reply_count,
        "content_kind": content_kind,
        "core_title_guess": parsed_title.core_title_guess,
        "chapter_name": parsed_title.chapter_name,
        "author_guess": parsed_title.author_guess,
        "group_name": parsed_title.group_name,
        "url": thread_url_from_tid(item.tid, base_url=base_url),
    }


def handle_remote_forums_list(handler, conn, settings: Settings):
    forums_repo = ForumsRepository(conn)
    rows = forums_repo.list_forums()
    json_response(handler, [
        {
            "forum_id": r["forum_id"],
            "name": r["name"],
            "name_en": r["name_en"] if "name_en" in r.keys() else None,
            "content_kind": r["content_kind"],
            "enabled": bool(r["enabled"]),
        }
        for r in rows
    ])


def handle_remote_forum_browse(handler, params, conn, settings: Settings):
    try:
        forum_id = int(params.get("forum_id", ["30"])[0])
        page = int(params.get("page", ["1"])[0])
    except (TypeError, ValueError):
        error_response(handler, "forum_id and page must be integers", HTTPStatus.BAD_REQUEST)
        return
    order = params.get("order", ["default"])[0]
    base_url = params.get("base_url", ["https://bbs.yamibo.com"])[0]

    if page <= 0:
        error_response(handler, "page must be positive", HTTPStatus.BAD_REQUEST)
        return

    forums_repo = ForumsRepository(conn)
    forum = forums_repo.get_forum(forum_id)
    if forum is None or not bool(forum["enabled"]):
        error_response(handler, f"forum {forum_id} not found or disabled", HTTPStatus.NOT_FOUND)
        return

    try:
        with _open_web_client(settings) as (_identity, client):
            if order == "dateline":
                result, items, total_pages = client.fetch_forum_threads_dateline(
                    page=page, base_url=base_url, forum_id=forum_id
                )
            else:
                result, items = client.fetch_forum_threads(
                    page=page, base_url=base_url, forum_id=forum_id
                )
                total_pages = extract_total_pages(result.html)
    except Exception as exc:
        status, code, message = _map_remote_error(exc)
        json_response(handler, {"error": message, "code": code}, HTTPStatus(status))
        return

    item_dicts = [
        _forum_thread_item_to_dict(item, base_url, content_kind=forum["content_kind"] if "content_kind" in forum.keys() else None)
        for item in items
        if not item.is_sticky and (item.category or "").strip() != "公告"
    ]
    _enrich_items(item_dicts, conn)

    json_response(handler, {
        "source": "remote",
        "forum_id": forum_id,
        "page": page,
        "order": order,
        "total_pages": total_pages,
        "final_url": result.final_url,
        "items": item_dicts,
    })


def handle_remote_thread_detail(handler, tid: int, params, conn, settings: Settings):
    try:
        page = int(params.get("page", ["1"])[0])
    except (TypeError, ValueError):
        page = 1
    base_url = params.get("base_url", ["https://bbs.yamibo.com"])[0]
    forum_id_raw = params.get("forum_id", [None])[0]

    if page <= 0:
        error_response(handler, "page must be positive", HTTPStatus.BAD_REQUEST)
        return

    try:
        with _open_web_client(settings) as (_identity, client):
            result = client.fetch_thread_page(tid=tid, page=page, base_url=base_url)
    except Exception as exc:
        status, code, message = _map_remote_error(exc)
        json_response(handler, {"error": message, "code": code}, HTTPStatus(status))
        return

    try:
        snapshot = parse_thread_snapshot(result.html, url=result.final_url, tid=tid)
    except Exception as exc:
        status, code, message = _map_remote_error(exc)
        json_response(handler, {"error": message, "code": code}, HTTPStatus(status))
        return

    try:
        detail_summary = parse_thread_detail(result.html, base_url=result.final_url)
    except Exception:
        detail_summary = None

    display_image_urls_by_pid = {
        floor.pid: list(floor.image_urls or [])
        for floor in (detail_summary.floors if detail_summary is not None else [])
    }

    repo = ThreadsRepository(conn)
    local_row = repo.get_thread(tid)

    resolved_forum_id = None
    if forum_id_raw is not None:
        try:
            resolved_forum_id = int(forum_id_raw)
        except (TypeError, ValueError):
            pass
    if resolved_forum_id is None and local_row and local_row["forum_id"] is not None:
        resolved_forum_id = int(local_row["forum_id"])

    content_kind = None
    if local_row and "content_kind" in local_row.keys():
        content_kind = local_row["content_kind"]

    floors = []
    for floor in snapshot.floors:
        image_slots = [
            {
                "remote_url": url,
                "local_path": None,
                "status": "remote",
            }
            for url in display_image_urls_by_pid.get(floor.pid, floor.image_urls or [])
        ]
        floors.append({
            "pid": floor.pid,
            "floor_no": floor.floor_no,
            "publisher": floor.publisher,
            "publisher_uid": floor.publisher_uid,
            "content": floor.content or "",
            "pub_time": floor.pub_time,
            "has_images": floor.has_images,
            "quote_text": floor.quote_text,
            "reply_text": floor.reply_text,
            "rich_body_html": _clean_rich_body(_rewrite_thread_links(floor.rich_body_html)) if floor.rich_body_html else None,
            "image_slots": image_slots,
        })

    json_response(handler, {
        "source": "remote",
        "tid": tid,
        "forum_id": resolved_forum_id,
        "page": page,
        "total_pages": None,
        "url": thread_page_url_from_tid(tid, page=page, base_url=base_url),
        "raw_title": snapshot.raw_title,
        "display_title": snapshot.display_title,
        "publisher": snapshot.publisher,
        "publisher_uid": snapshot.publisher_uid,
        "pub_time": snapshot.pub_time,
        "content_kind": content_kind,
        "archive_status": local_row["archive_status"] if local_row else None,
        "local_thread": _build_local_thread_dict(local_row),
        "floors": floors,
    })


def handle_remote_image_proxy(handler, params, settings: Settings):
    raw_url = params.get("url", [""])[0]
    if not raw_url:
        error_response(handler, "url required", HTTPStatus.BAD_REQUEST)
        return

    if not _is_allowed_remote_image_url(raw_url):
        error_response(handler, "only public http(s) image URLs are supported", HTTPStatus.BAD_REQUEST)
        return

    try:
        with _open_web_client(settings) as (_identity, client):
            req = urllib.request.Request(raw_url, headers=client._request_headers(referer="https://bbs.yamibo.com/"))
            with client.opener.open(req, timeout=client.timeout) as resp:
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                body = resp.read()
    except Exception as exc:
        error_response(handler, f"failed to fetch image: {exc}", HTTPStatus.BAD_GATEWAY)
        return

    try:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "public, max-age=86400")
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass
