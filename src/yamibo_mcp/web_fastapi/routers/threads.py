from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.application.archive_commands import create_thread_archive_batch_jobs
from yamibo_mcp.application.update_commands import create_update_thread_job
from yamibo_mcp.application.update_queries import check_thread_updates
from yamibo_mcp.maintenance.cleanup_data import remove_thread_dir
from yamibo_mcp.server.agent_adapter import to_wire
from yamibo_mcp.web_fastapi.converters import (
    thread_summary_dict, floor_to_dict, asset_to_dict, block_to_dict, clean_rich_body_html,
)
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.web_fastapi.helpers import jsonish_loads
from yamibo_mcp.yamibo.urls import remote_image_identity, thread_url_from_tid, thread_author_url_from_tid

router = APIRouter(prefix="/api", tags=["threads"])

EXPORTABLE_FORUMS = {30, 55}


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


@router.get("/threads")
def list_threads(
    q: str | None = Query(default=None),
    forum_id: int | None = Query(default=None),
    days: int | None = Query(default=None),
    archive_status: str | None = Query(default=None),
    sort_key: str = Query(default="sync_time"),
    sort_dir: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    conn: DatabaseConnection = Depends(get_conn),
):
    repo = ThreadsRepository(conn)
    if archive_status == "all":
        archive_status = None
    page_result = repo.list_threads_page(
        page=page, page_size=page_size,
        q=q or None, forum_id=forum_id, days=days,
        archive_status=archive_status or None,
        sort_key=sort_key, sort_dir=sort_dir,
    )
    return {
        "page": page_result["page"], "page_size": page_result["page_size"],
        "total_count": page_result["total_count"], "total_pages": page_result["total_pages"],
        "q": q, "forum_id": forum_id, "days": days,
        "archive_status": archive_status or None,
        "sort_key": sort_key, "sort_dir": sort_dir,
        "items": [thread_summary_dict(r) for r in page_result["items"]],
    }


@router.get("/threads/{tid}/active-sync-job")
def active_sync_job(tid: int, conn: DatabaseConnection = Depends(get_conn)):
    """Return the current sync job for one thread without loading the job list."""
    job = JobsRepository(conn).find_live_job_for_thread(job_type="sync_thread", tid=tid)
    if job is None:
        return {"job": None}
    return {
        "job": {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "tid": job.tid,
            "status": job.status,
            "stage": job.stage,
            "updated_at": job.updated_at,
        }
    }


@router.get("/threads/{tid}")
def thread_detail(
    tid: int,
    preview_page: int | None = Query(default=None),
    preview_page_size: int | None = Query(default=None),
    conn: DatabaseConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    repo = ThreadsRepository(conn)
    rag_repo = RagChunksRepository(conn)
    thread = repo.get_thread(tid)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

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

    # Image retry actions need the stable asset identity.  Enrich the
    # metadata-derived slots without making the browser infer an asset ID
    # from a possibly stale/misordered local path.
    asset_ids_by_identity = {
        remote_image_identity(str(row["remote_url"])): str(row["asset_id"])
        for row in AssetsRepository(conn).list_assets(tid)
        if row["remote_url"]
    }
    for floor in data["floors"]:
        for slot in floor.get("image_slots") or []:
            if not isinstance(slot, dict) or not slot.get("remote_url"):
                continue
            asset_id = asset_ids_by_identity.get(remote_image_identity(str(slot["remote_url"])))
            if asset_id is not None:
                slot["asset_id"] = asset_id

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
    return data


@router.get("/threads/{tid}/images")
def thread_images(tid: int, settings: Settings = Depends(get_settings)):
    meta_path = settings.data_dir / "threads" / str(tid) / "metadata.json"
    if not meta_path.exists():
        return []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
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
                    "pid": pid, "url": url, "source": key,
                    "is_content": key == "content_image_urls",
                    "is_shared": key == "shared_image_urls",
                })
    return images


@router.get("/threads/{tid}/assets")
def thread_assets(tid: int, conn: DatabaseConnection = Depends(get_conn)):
    assets = AssetsRepository(conn).list_assets(tid)
    return [asset_to_dict(a) for a in assets]


@router.post("/threads/{tid}/images/{asset_id}/retry")
def retry_thread_image(
    tid: int,
    asset_id: str,
    conn: DatabaseConnection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
):
    """Queue one URL for an interactive, selected image backfill.

    The source Job is never rerun or removed.  An already-live identical
    selected Job is returned to make repeated UI clicks idempotent.
    """
    thread = ThreadsRepository(conn).get_thread(tid)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    asset = AssetsRepository(conn).get_asset(asset_id)
    if asset is None or int(asset["tid"]) != int(tid):
        raise HTTPException(status_code=404, detail="Image asset not found")
    remote_url = str(asset["remote_url"] or "").strip()
    if not remote_url or not remote_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Image asset has no retryable remote URL")

    target_positions: list[dict[str, object]] = []
    metadata = _load_thread_archive_metadata(settings, tid)
    for floor in metadata.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        urls = list(floor.get("remote_image_urls") or floor.get("image_urls") or [])
        for index, candidate in enumerate(urls, start=1):
            if remote_image_identity(str(candidate)) != remote_image_identity(remote_url):
                continue
            if asset["pid"] is not None and floor.get("pid") is not None and int(asset["pid"]) != int(floor["pid"]):
                continue
            target_positions.append({
                "pid": int(floor["pid"]) if floor.get("pid") is not None else int(asset["pid"]),
                "floor_no": int(floor["floor_no"]) if floor.get("floor_no") is not None else 1,
                "image_index": index,
                "url": remote_url,
            })
            break

    payload = {
        "tid": int(tid),
        "dry_run": False,
        "scope": "selected",
        "target_asset_id": asset_id,
        "target_urls": [remote_url],
        "include_first_floor": True,
        "priority": "interactive",
    }
    if target_positions:
        payload["target_positions"] = target_positions

    live_statuses = ("queued", "running", "retrying", "paused", "cancel_requested", "interrupted")
    placeholders = ",".join("?" for _ in live_statuses)
    rows = conn.execute(
        f"SELECT job_id, payload_json, status FROM jobs WHERE job_type = ? AND tid = ? AND status IN ({placeholders}) ORDER BY created_at DESC",
        ("image_backfill", int(tid), *live_statuses),
    ).fetchall()
    for row in rows:
        try:
            existing_payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            existing_payload = {}
        if (
            existing_payload.get("scope") == "selected"
            and str(existing_payload.get("target_asset_id") or "") == asset_id
            and list(existing_payload.get("target_urls") or []) == [remote_url]
        ):
            return {"ok": True, "job_id": row["job_id"], "status": row["status"], "created": False}

    job = JobsRepository(conn).create("image_backfill", tid=int(tid), payload=payload)
    return {"ok": True, "job_id": job.job_id, "status": job.status, "created": True}


@router.get("/threads/{tid}/blocks")
def thread_blocks(tid: int, conn: DatabaseConnection = Depends(get_conn)):
    blocks = ContentBlocksRepository(conn).list_blocks(tid)
    return [block_to_dict(b) for b in blocks]


@router.get("/threads/{tid}/update-check")
def thread_update_check(tid: int):
    result = check_thread_updates(tid=tid)
    if result.get("status") == "failed" and result.get("reason") == f"thread {tid} not found":
        raise HTTPException(status_code=404, detail="Thread not found")
    return result


@router.post("/threads/resync")
def resync_thread(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    tid = body.get("tid")
    if not tid:
        raise HTTPException(status_code=400, detail="tid required")
    payload = {"tid": int(tid)}
    if body.get("forum_id") is not None:
        payload["forum_id"] = int(body["forum_id"])
    job = JobsRepository(conn).create("sync_thread", tid=int(tid), payload=payload)
    return {"ok": True, "job_id": job.job_id}


@router.post("/threads/resync-batch")
def resync_threads_batch(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    tids = body.get("tids", [])
    if not tids:
        raise HTTPException(status_code=400, detail="tids required")
    base_url = body.get("base_url") or None
    result = create_thread_archive_batch_jobs(tids=[int(tid) for tid in tids], base_url=base_url)
    return {"ok": True, **(result.data or {})}


@router.post("/threads/update")
def update_thread(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    tid = body.get("tid")
    if not tid:
        raise HTTPException(status_code=400, detail="tid required")
    base_url = body.get("base_url") or None
    try:
        result = create_update_thread_job(tid=int(tid), base_url=base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "job_id": result["job_id"], "created": result.get("created", True)}


@router.post("/threads/export")
def export_thread(body: dict, conn: DatabaseConnection = Depends(get_conn), settings: Settings = Depends(get_settings)):
    tid = body.get("tid")
    if not tid:
        raise HTTPException(status_code=400, detail="tid required")
    forum_id = body.get("forum_id")
    if forum_id is not None:
        forum_id = int(forum_id)
    else:
        row = ThreadsRepository(conn).get_thread(int(tid))
        forum_id = row["forum_id"] if row and row["forum_id"] is not None else None
    if forum_id is not None and forum_id not in EXPORTABLE_FORUMS:
        raise HTTPException(status_code=400, detail="仅漫画区和轻小说区的贴子支持导出")
    strategy = body.get("strategy") or settings.export_default_strategy
    payload = {"tid": int(tid), "strategy": strategy}
    if forum_id is not None:
        payload["forum_id"] = forum_id
    job = JobsRepository(conn).create("export_thread", tid=int(tid), payload=payload)
    return {"ok": True, "job_id": job.job_id}


def _delete_thread_record(conn, settings: Settings, tid: int) -> tuple:
    repo = ThreadsRepository(conn)
    before, after = repo.delete_thread(tid)
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is not None:
        remove_thread_dir(data_dir=data_dir, tid=tid, dry_run=False)
    deleted_series = None
    series_id = (before.get("thread") or {}).get("series_id")
    if series_id:
        remaining = ThreadsRepository(conn).count_threads_for_series(int(series_id))
        if remaining == 0:
            from yamibo_mcp.db.repositories.series import SeriesRepository as _SeriesRepository
            _SeriesRepository(conn).delete_series(int(series_id))
            deleted_series = int(series_id)
    return before, after, deleted_series


@router.post("/threads/delete")
def delete_thread(body: dict, conn: DatabaseConnection = Depends(get_conn), settings: Settings = Depends(get_settings)):
    from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
    tid = body.get("tid")
    if not tid:
        raise HTTPException(status_code=400, detail="tid required")
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
        return resp
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/threads/batch-delete")
def batch_delete_threads(body: dict, conn: DatabaseConnection = Depends(get_conn), settings: Settings = Depends(get_settings)):
    from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
    tids = body.get("tids", [])
    if not tids:
        raise HTTPException(status_code=400, detail="tids required")
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
        return {"ok": True, "deleted": deleted, "tids": unique_tids, "deleted_series_ids": deleted_series_ids}
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/threads/update-chapter")
def update_chapter(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    tid = body.get("tid")
    if not tid:
        raise HTTPException(status_code=400, detail="tid required")
    ThreadsRepository(conn).update_chapter_info(
        int(tid),
        chapter_name=body.get("chapter_name"),
        chapter_index=body.get("chapter_index"),
        author_guess=body.get("author_guess"),
        group_name=body.get("group_name"),
    )
    conn.commit()
    return {"ok": True, "tid": int(tid)}


@router.post("/threads/archive-batch")
def archive_threads_batch(body: dict):
    raw_tids = body.get("tids")
    if not isinstance(raw_tids, list):
        raise HTTPException(status_code=400, detail="tids required")
    tids = [int(value) for value in raw_tids if value not in {None, ""}]
    result = create_thread_archive_batch_jobs(
        tids=tids,
        base_url=str(body.get("base_url")) if body.get("base_url") not in {None, ""} else None,
        forum_id=int(body["forum_id"]) if body.get("forum_id") not in {None, ""} else None,
    )
    if not result.ok or result.data is None:
        payload = to_wire(result)
        return payload
    return {"ok": True, **result.data}


@router.get("/exports")
def exports_list(conn: DatabaseConnection = Depends(get_conn)):
    rows = ThreadsRepository(conn).list_exports(limit=200)
    return [thread_summary_dict(r) for r in rows]
