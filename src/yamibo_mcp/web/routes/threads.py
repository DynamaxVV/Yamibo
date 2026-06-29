from __future__ import annotations

import json
from http import HTTPStatus

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.application.archive_commands import create_thread_archive_batch_jobs
from yamibo_mcp.application.update_queries import check_thread_updates
from yamibo_mcp.maintenance.cleanup_data import remove_thread_dir
from yamibo_mcp.yamibo.urls import thread_url_from_tid, thread_author_url_from_tid
from ._helpers import json_response, error_response, read_json_body, jsonish_loads
from ._converters import (
    thread_summary_dict, floor_to_dict, asset_to_dict, block_to_dict, clean_rich_body_html,
)

EXPORTABLE_FORUMS = {30, 55}  # 漫画区, 轻小说区


def _load_thread_archive_metadata(settings: Settings, tid: int) -> dict:
    meta_path = settings.data_dir / "threads" / str(tid) / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load_thread_archive_summary(settings: Settings, tid: int) -> dict:
    meta = _load_thread_archive_metadata(settings, tid)
    if not meta:
        return {}
    return {
        "context_path": meta.get("context_path"),
        "archived_images": meta.get("archived_images") or {},
        "non_export_images": meta.get("non_export_images") or {},
        "shared_images": meta.get("shared_images") or {},
        "skipped_image_urls": meta.get("skipped_image_urls") or {},
        "missing_image_urls": meta.get("missing_image_urls") or [],
        "missing_shared_image_urls": meta.get("missing_shared_image_urls") or [],
    }


def handle_threads_list(handler, params, conn):
    q = params.get("q", [""])[0].strip()
    forum_id = params.get("forum_id", [None])[0]
    days = params.get("days", [None])[0]
    archive_status = (params.get("archive_status", [""])[0] or "").strip()
    sort_key = (params.get("sort_key", ["sync_time"])[0] or "sync_time").strip()
    sort_dir = (params.get("sort_dir", ["desc"])[0] or "desc").strip()
    try:
        page = max(int(params.get("page", ["1"])[0]), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(max(int(params.get("page_size", ["50"])[0]), 1), 200)
    except (TypeError, ValueError):
        page_size = 50
    repo = ThreadsRepository(conn)
    fid = int(forum_id) if forum_id else None
    try:
        days_value = int(days) if days else None
    except (TypeError, ValueError):
        days_value = None
    if archive_status == "all":
        archive_status = ""
    page_result = repo.list_threads_page(
        page=page,
        page_size=page_size,
        q=q or None,
        forum_id=fid,
        days=days_value,
        archive_status=archive_status or None,
        sort_key=sort_key,
        sort_dir=sort_dir,
    )
    json_response(handler, {
        "page": page_result["page"],
        "page_size": page_result["page_size"],
        "total_count": page_result["total_count"],
        "total_pages": page_result["total_pages"],
        "q": q,
        "forum_id": fid,
        "days": days_value,
        "archive_status": archive_status or None,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "items": [thread_summary_dict(r) for r in page_result["items"]],
    })


def handle_thread_detail(handler, tid, conn, params, settings):
    repo = ThreadsRepository(conn)
    rag_repo = RagChunksRepository(conn)
    thread = repo.get_thread(tid)
    if thread is None:
        error_response(handler, "Thread not found", HTTPStatus.NOT_FOUND)
        return
    preview_page_raw = params.get("preview_page", [None])[0] if params else None
    preview_page_size_raw = params.get("preview_page_size", [None])[0] if params else None
    preview_page = int(preview_page_raw) if preview_page_raw else None
    preview_page_size = int(preview_page_size_raw) if preview_page_size_raw else None
    is_novel = (thread["content_kind"] if "content_kind" in thread.keys() else None) == "novel"
    if is_novel and preview_page and preview_page_size and preview_page > 0 and preview_page_size > 0:
        floor_count = repo.count_floors(tid)
        offset = (preview_page - 1) * preview_page_size
        floors = repo.list_floors_page(tid, limit=preview_page_size, offset=offset)
    else:
        floor_count = repo.count_floors(tid)
        floors = repo.list_floors(tid)
    title_row = repo.get_title_parse(tid)
    data = thread_summary_dict(thread)
    if title_row:
        if data.get("chapter_name") is None:
            data["chapter_name"] = title_row["chapter_name"] if "chapter_name" in title_row.keys() else None
        if data.get("chapter_index") is None:
            data["chapter_index"] = title_row["chapter_index"] if "chapter_index" in title_row.keys() else None
        data["group_name"] = title_row["group_name"] if "group_name" in title_row.keys() else None
        data["author_guess"] = title_row["author_guess"] if "author_guess" in title_row.keys() else None
    publisher_uid = thread["publisher_uid"] if "publisher_uid" in thread.keys() else None
    content_kind = thread["content_kind"] if "content_kind" in thread.keys() else None
    data["url"] = (
        thread_author_url_from_tid(tid, author_uid=str(publisher_uid))
        if content_kind == "novel" and publisher_uid
        else thread_url_from_tid(tid)
    )
    data["floors"] = [floor_to_dict(f) for f in floors]
    archive_meta = _load_thread_archive_metadata(settings, tid)
    rich_body_map: dict[int, str] = {}
    for floor_meta in archive_meta.get("floors") or []:
        if not isinstance(floor_meta, dict):
            continue
        pid = floor_meta.get("pid")
        rich_body_html = floor_meta.get("rich_body_html")
        if pid is None or not rich_body_html:
            continue
        try:
            rich_body_map[int(pid)] = clean_rich_body_html(str(rich_body_html)) or ""
        except (TypeError, ValueError):
            continue
    floor_meta_map: dict[int, dict] = {}
    for floor_meta in archive_meta.get("floors") or []:
        if not isinstance(floor_meta, dict):
            continue
        pid = floor_meta.get("pid")
        try:
            floor_meta_map[int(pid)] = floor_meta
        except (TypeError, ValueError):
            continue
    if rich_body_map:
        for floor in data["floors"]:
            rich_body_html = rich_body_map.get(floor["pid"])
            if rich_body_html:
                floor["rich_body_html"] = rich_body_html
    for floor in data["floors"]:
        floor_meta = floor_meta_map.get(floor["pid"]) or {}
        floor["remote_image_urls"] = list(floor_meta.get("remote_image_urls") or [])
        floor["missing_image_urls"] = list(floor_meta.get("missing_image_urls") or [])
        floor["image_slots"] = list(floor_meta.get("image_slots") or [])
    data["floor_count"] = floor_count
    data["floor_page"] = preview_page
    data["floor_page_size"] = preview_page_size
    data["floor_total_pages"] = (floor_count + preview_page_size - 1) // preview_page_size if preview_page_size else None
    data["publisher_uid"] = publisher_uid
    data["pub_time"] = thread["pub_time"] if "pub_time" in thread.keys() else None
    data["image_count"] = thread["image_count"] if "image_count" in thread.keys() else 0
    data["primary_media_type"] = thread["primary_media_type"] if "primary_media_type" in thread.keys() else None
    data["archive_summary"] = _load_thread_archive_summary(settings, tid)
    if "missing_images_json" in thread.keys():
        try:
            data["missing_image_urls"] = jsonish_loads(thread["missing_images_json"], [])
        except ValueError:
            data["missing_image_urls"] = []
    else:
        data["missing_image_urls"] = []
    series_id = thread["series_id"] if "series_id" in thread.keys() else None
    if series_id:
        series_row = SeriesRepository(conn).get_series(int(series_id))
        data["series_title"] = series_row["canonical_title"] if series_row else None
    else:
        data["series_title"] = None
    rag_row = rag_repo.count_thread_rag_stats(tid)
    latest_rag_job = JobsRepository(conn).get_latest_job(job_type="rag_index", tid=tid)
    data["rag_summary"] = {
        "enabled": bool(getattr(settings, "rag_enabled", False)),
        "chunk_count": rag_row["chunk_count"],
        "indexed_chunk_count": rag_row["indexed_chunk_count"],
        "pending_chunk_count": rag_row["pending_chunk_count"],
        "failed_chunk_count": rag_row["failed_chunk_count"],
        "last_indexed_at": rag_row["last_indexed_at"],
        "latest_job": None if latest_rag_job is None else {
            "job_id": latest_rag_job.job_id,
            "status": latest_rag_job.status,
            "stage": latest_rag_job.stage,
            "updated_at": latest_rag_job.updated_at,
            "created_at": latest_rag_job.created_at,
        },
    }
    json_response(handler, data)


def handle_thread_images(handler, tid, conn, settings):
    """Read images from metadata.json since assets table may be empty."""
    from pathlib import Path as _Path
    meta_path = settings.data_dir / "threads" / str(tid) / "metadata.json"
    if not meta_path.exists():
        json_response(handler, [])
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        json_response(handler, [])
        return
    images = []
    floors = meta.get("floors", [])
    for floor in floors:
        pid = floor.get("pid")
        for key in ("content_image_urls", "image_urls", "shared_image_urls", "skipped_image_urls"):
            urls = floor.get(key) or []
            for url in urls:
                if not isinstance(url, str) or not url.strip():
                    continue
                if any(img["url"] == url for img in images):
                    continue
                images.append({
                    "pid": pid,
                    "url": url,
                    "source": key,
                    "is_content": key == "content_image_urls",
                    "is_shared": key == "shared_image_urls",
                })
    json_response(handler, images)


def handle_thread_assets(handler, tid, conn):
    assets = AssetsRepository(conn).list_assets(tid)
    json_response(handler, [asset_to_dict(a) for a in assets])


def handle_thread_blocks(handler, tid, conn):
    blocks = ContentBlocksRepository(conn).list_blocks(tid)
    json_response(handler, [block_to_dict(b) for b in blocks])


def handle_thread_update_check(handler, tid: int, settings: Settings) -> None:
    result = check_thread_updates(tid=tid)
    if result.get("status") == "failed" and result.get("reason") == f"thread {tid} not found":
        error_response(handler, "Thread not found", HTTPStatus.NOT_FOUND)
        return
    json_response(handler, result)


def handle_resync_thread(handler, conn):
    body = read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        error_response(handler, "tid required")
        return
    payload = {"tid": int(tid)}
    if body.get("forum_id") is not None:
        payload["forum_id"] = int(body["forum_id"])
    job = JobsRepository(conn).create("sync_thread", tid=int(tid), payload=payload)
    json_response(handler, {"ok": True, "job_id": job.job_id})


def handle_resync_threads_batch(handler, conn):
    body = read_json_body(handler)
    tids = body.get("tids", [])
    if not tids:
        error_response(handler, "tids required")
        return
    base_url = body.get("base_url") or None
    result = create_thread_archive_batch_jobs(tids=[int(tid) for tid in tids], base_url=base_url)
    json_response(handler, {"ok": True, **(result.data or {})})


def handle_update_thread(handler, conn):
    body = read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        error_response(handler, "tid required")
        return
    payload = {"tid": int(tid)}
    if body.get("base_url"):
        payload["base_url"] = body["base_url"]
    job = JobsRepository(conn).create("update_thread", tid=int(tid), payload=payload)
    json_response(handler, {"ok": True, "job_id": job.job_id})


def handle_export_thread(handler, conn, settings):
    body = read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        error_response(handler, "tid required")
        return
    forum_id = body.get("forum_id")
    if forum_id is not None:
        forum_id = int(forum_id)
    else:
        row = ThreadsRepository(conn).get_thread(int(tid))
        forum_id = row["forum_id"] if row and row["forum_id"] is not None else None
    if forum_id is not None and forum_id not in EXPORTABLE_FORUMS:
        error_response(handler, "仅漫画区和轻小说区的贴子支持导出")
        return
    strategy = body.get("strategy") or settings.export_default_strategy
    payload = {"tid": int(tid), "strategy": strategy}
    if forum_id is not None:
        payload["forum_id"] = forum_id
    job = JobsRepository(conn).create("export_thread", tid=int(tid), payload=payload)
    json_response(handler, {"ok": True, "job_id": job.job_id})


def _delete_thread_record(conn, settings: Settings, tid: int) -> tuple[dict[str, object], dict[str, object], int | None]:
    repo = ThreadsRepository(conn)
    before, after = repo.delete_thread(tid)
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is not None:
        remove_thread_dir(data_dir=data_dir, tid=tid, dry_run=False)
    deleted_series = None
    series_id = before.get("series_id")
    if series_id:
        remaining = ThreadsRepository(conn).count_threads_for_series(int(series_id))
        if remaining == 0:
            from yamibo_mcp.db.repositories.series import SeriesRepository as _SeriesRepository
            _SeriesRepository(conn).delete_series(int(series_id))
            deleted_series = int(series_id)
    return before, after, deleted_series


def handle_delete_thread(handler, conn, settings):
    from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
    body = read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        error_response(handler, "tid required")
        return
    try:
        before, after, deleted_series = _delete_thread_record(conn, settings, int(tid))
        AuditEventsRepository(conn).record(
            actor="web", action="delete_thread", target_type="thread",
            target_id=str(tid), before=before, after=after,
        )
        conn.commit()
        resp: dict = {"ok": True, "tid": int(tid)}
        if deleted_series is not None:
            resp["deleted_series_id"] = deleted_series
        json_response(handler, resp)
    except ValueError as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def handle_batch_delete_threads(handler, conn, settings):
    from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
    body = read_json_body(handler)
    tids = body.get("tids", [])
    if not tids:
        error_response(handler, "tids required")
        return
    try:
        deleted = 0
        deleted_series_ids: list[int] = []
        audit_repo = AuditEventsRepository(conn)
        unique_tids = list(dict.fromkeys(int(tid) for tid in tids))
        for tid in unique_tids:
            before, after, deleted_series = _delete_thread_record(conn, settings, tid)
            audit_repo.record(
                actor="web", action="delete_thread", target_type="thread",
                target_id=str(tid), before=before, after=after,
            )
            deleted += 1
            if deleted_series is not None:
                deleted_series_ids.append(int(deleted_series))
        conn.commit()
        json_response(handler, {
            "ok": True,
            "deleted": deleted,
            "tids": unique_tids,
            "deleted_series_ids": deleted_series_ids,
        })
    except ValueError as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def handle_update_chapter(handler, conn):
    body = read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        error_response(handler, "tid required")
        return
    ThreadsRepository(conn).update_chapter_info(
        int(tid),
        chapter_name=body.get("chapter_name"),
        chapter_index=body.get("chapter_index"),
        author_guess=body.get("author_guess"),
        group_name=body.get("group_name"),
    )
    conn.commit()
    json_response(handler, {"ok": True, "tid": int(tid)})


def handle_archive_threads_batch(handler):
    body = read_json_body(handler)
    raw_tids = body.get("tids")
    if not isinstance(raw_tids, list):
        error_response(handler, "tids required")
        return
    tids = [int(value) for value in raw_tids if value not in {None, ""}]
    result = create_thread_archive_batch_jobs(
        tids=tids,
        base_url=str(body.get("base_url")) if body.get("base_url") not in {None, ""} else None,
        forum_id=int(body["forum_id"]) if body.get("forum_id") not in {None, ""} else None,
    )
    if not result.ok or result.data is None:
        payload = to_wire(result)
        status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_REQUEST
        json_response(handler, payload, status)
        return
    json_response(handler, {"ok": True, **result.data})


def handle_exports_list(handler, conn):
    rows = ThreadsRepository(conn).list_exports(limit=200)
    json_response(handler, [thread_summary_dict(r) for r in rows])
