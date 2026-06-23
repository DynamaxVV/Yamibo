from __future__ import annotations

import json
import re
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse
from urllib.parse import quote

from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.config import Settings
from yamibo_mcp.application.archive_commands import create_thread_archive_batch_jobs
from yamibo_mcp.application.rag_commands import create_rag_index_batch_jobs, create_rag_index_job
from yamibo_mcp.application.rag_queries import search_archived_content
from yamibo_mcp.maintenance.forum_sizes import read_forum_size_cache, refresh_forum_size_cache
from yamibo_mcp.application.update_queries import check_thread_updates
from yamibo_mcp.server.agent_adapter import to_wire
from yamibo_mcp.services.title_hints import update_title_hints
from yamibo_mcp.yamibo.parsers.thread_detail import normalize_rich_body_html
from yamibo_mcp.yamibo.urls import thread_author_url_from_tid, thread_url_from_tid


def _json_response(handler, data, status=HTTPStatus.OK):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status.value)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        return


def _read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw)


def _error_response(handler, message, status=HTTPStatus.BAD_REQUEST):
    _json_response(handler, {"error": message}, status)


def handle_api(handler, path: str, query: str, settings: Settings) -> bool:
    """Handle /api/* routes. Returns True if handled."""
    if not path.startswith("/api/"):
        return False

    route = path[4:]  # strip /api
    params = parse_qs(query)
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        _route(handler, route, params, conn, settings)
    except (BrokenPipeError, ConnectionResetError):
        return True
    except Exception as exc:
        _error_response(handler, str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
    finally:
        conn.close()
    return True


def _route(handler, route: str, params, conn, settings):
    if route == "/dashboard":
        _dashboard(handler, conn, params)
    elif route == "/jobs" and handler.command == "GET":
        _jobs_list(handler, params, conn)
    elif route == "/jobs/counts" and handler.command == "GET":
        _jobs_counts(handler, conn)
    elif route.startswith("/jobs/") and route.endswith("/events"):
        job_id = route[6:-7]
        _job_events(handler, job_id, conn)
    elif route.startswith("/jobs/") and handler.command == "GET":
        job_id = route[6:]
        _job_detail(handler, job_id, conn)
    elif route == "/jobs/delete" and handler.command == "POST":
        _delete_job(handler, conn)
    elif route == "/jobs/retry" and handler.command == "POST":
        _retry_job(handler, conn)
    elif route == "/jobs/pause" and handler.command == "POST":
        _pause_job(handler, conn)
    elif route == "/jobs/resume" and handler.command == "POST":
        _resume_job(handler, conn)
    elif route == "/jobs/batch-delete" and handler.command == "POST":
        _batch_delete_jobs(handler, conn)
    elif route == "/jobs/batch-delete-ids" and handler.command == "POST":
        _batch_delete_jobs_by_ids(handler, conn)
    elif route == "/jobs/safe-delete" and handler.command == "POST":
        _safe_delete_job(handler, conn)
    elif route == "/threads" and handler.command == "GET":
        _threads_list(handler, params, conn)
    elif route.startswith("/threads/") and route.endswith("/images"):
        tid = int(route[9:-7])
        _thread_images(handler, tid, conn, settings)
    elif route.startswith("/threads/") and route.endswith("/assets"):
        tid = int(route[9:-7])
        _thread_assets(handler, tid, conn)
    elif route.startswith("/threads/") and route.endswith("/blocks"):
        tid = int(route[9:-7])
        _thread_blocks(handler, tid, conn)
    elif route.startswith("/threads/") and route.endswith("/update-check") and handler.command == "GET":
        tid = int(route[9:-13])
        _thread_update_check(handler, tid, settings)
    elif route.startswith("/threads/") and handler.command == "GET":
        tid = int(route[9:])
        _thread_detail(handler, tid, conn, params, settings)
    elif route == "/series" and handler.command == "GET":
        _series_list(handler, conn)
    elif route == "/series/delete" and handler.command == "POST":
        _delete_series(handler, conn, settings)
    elif route.startswith("/series/") and route.endswith("/similar"):
        series_id = int(route[8:-8])
        _similar_series(handler, series_id, conn)
    elif route.startswith("/series/") and handler.command == "GET":
        series_id = int(route[8:])
        _series_detail(handler, series_id, conn)
    elif route == "/forums" and handler.command == "GET":
        _forums_list(handler, conn, settings)
    elif route == "/forums/refresh-size-cache" and handler.command == "POST":
        _refresh_forum_size_cache(handler, conn, settings)
    elif route == "/exports" and handler.command == "GET":
        _exports_list(handler, conn)
    elif route == "/fonts" and handler.command == "GET":
        _fonts_list(handler, settings)
    elif route == "/rag/overview" and handler.command == "GET":
        _rag_overview(handler, conn, settings)
    elif route == "/rag/threads" and handler.command == "GET":
        _rag_threads(handler, params, conn)
    elif route == "/rag/index" and handler.command == "POST":
        _rag_index(handler)
    elif route == "/rag/index-batch" and handler.command == "POST":
        _rag_index_batch(handler, conn)
    elif route == "/rag/search" and handler.command == "POST":
        _rag_search(handler)
    elif route == "/review" and handler.command == "GET":
        _review_items(handler, conn)
    elif route == "/review/confirm-series" and handler.command == "POST":
        _confirm_series(handler, conn, settings)
    elif route == "/review/merge-series" and handler.command == "POST":
        _merge_series(handler, conn, settings)
    elif route == "/review/confirm-title" and handler.command == "POST":
        _confirm_title(handler, conn, settings)
    elif route == "/review/rebuild-series" and handler.command == "POST":
        _rebuild_series(handler, conn, settings)
    elif route == "/review/update-title" and handler.command == "POST":
        _update_title(handler, conn, settings)
    elif route == "/threads/update-chapter" and handler.command == "POST":
        _update_chapter(handler, conn)
    elif route == "/review/update-series" and handler.command == "POST":
        _update_series(handler, conn, settings)
    elif route == "/threads/resync" and handler.command == "POST":
        _resync_thread(handler, conn)
    elif route == "/threads/resync-batch" and handler.command == "POST":
        _resync_threads_batch(handler, conn)
    elif route == "/threads/archive-batch" and handler.command == "POST":
        _archive_threads_batch(handler)
    elif route == "/threads/update" and handler.command == "POST":
        _update_thread(handler, conn)
    elif route == "/threads/export" and handler.command == "POST":
        _export_thread(handler, conn, settings)
    elif route == "/threads/delete" and handler.command == "POST":
        _delete_thread(handler, conn, settings)
    elif route == "/threads/batch-delete" and handler.command == "POST":
        _batch_delete_threads(handler, conn, settings)
    elif route == "/debug/info" and handler.command == "GET":
        _debug_info(handler, conn, settings)
    elif route == "/logs" and handler.command == "GET":
        _logs(handler, params)
    else:
        _error_response(handler, f"Not found: {route}", HTTPStatus.NOT_FOUND)


def _dashboard(handler, conn, params):
    jobs_repo = JobsRepository(conn)
    threads_repo = ThreadsRepository(conn)
    thread_count = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    series_count = conn.execute("SELECT COUNT(*) FROM series").fetchone()[0]
    export_count = conn.execute("SELECT COUNT(*) FROM threads WHERE is_exported = 1").fetchone()[0]
    forum_rows = conn.execute(
        "SELECT forum_id, COUNT(*) as cnt FROM threads WHERE forum_id IS NOT NULL GROUP BY forum_id ORDER BY cnt DESC"
    ).fetchall()
    forum_counts = {r["forum_id"]: r["cnt"] for r in forum_rows}

    recent_limit = int(params.get("limit", ["10"])[0])
    recent_jobs = [_job_to_dict(j, conn) for j in jobs_repo.list(limit=10)]
    live_thread_statuses = _live_sync_thread_statuses(conn)
    workers = _worker_heartbeats(conn)
    audits = [_audit_to_dict(r) for r in AuditEventsRepository(conn).list_recent(limit=8)]
    recent_threads = [_thread_summary_dict(r) for r in threads_repo.list_threads(limit=recent_limit)]

    _json_response(handler, {
        "thread_count": thread_count,
        "series_count": series_count,
        "export_count": export_count,
        "forum_counts": forum_counts,
        "recent_jobs": recent_jobs,
        "live_thread_statuses": live_thread_statuses,
        "workers": workers,
        "recent_audits": audits,
        "recent_threads": recent_threads,
    })


_SYNC_THREAD_LIVE_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.CANCEL_REQUESTED.value,
    JobStatus.INTERRUPTED.value,
}


def _live_sync_thread_statuses(conn) -> dict[int, str]:
    rows = conn.execute(
        """
        SELECT tid, status
        FROM jobs
        WHERE job_type = 'sync_thread'
          AND tid IS NOT NULL
          AND status IN (?, ?, ?, ?, ?)
        ORDER BY updated_at DESC, created_at DESC
        """,
        tuple(sorted(_SYNC_THREAD_LIVE_STATUSES)),
    ).fetchall()
    statuses: dict[int, str] = {}
    for row in rows:
        tid = row["tid"]
        if tid is None or tid in statuses:
            continue
        statuses[int(tid)] = row["status"]
    return statuses


def _jobs_list(handler, params, conn):
    status = params.get("status", [None])[0]
    jobs = JobsRepository(conn).list(limit=150, status=status)
    _json_response(handler, [_job_to_dict(j, conn) for j in jobs])


def _jobs_counts(handler, conn):
    rows = conn.execute("SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status").fetchall()
    counts = {"all": 0}
    for r in rows:
        counts[r["status"]] = r["cnt"]
        counts["all"] += r["cnt"]
    _json_response(handler, counts)


def _job_detail(handler, job_id, conn):
    job = JobsRepository(conn).get(job_id)
    _json_response(handler, _job_to_dict(job, conn))


def _job_events(handler, job_id, conn):
    events = JobEventsRepository(conn).list(job_id=job_id, limit=200)
    _json_response(handler, [_event_to_dict(e) for e in events])


def _delete_job(handler, conn):
    body = _read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        _error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job is None:
        _error_response(handler, "Job not found", HTTPStatus.NOT_FOUND)
        return
    if job.status in ("running",):
        _error_response(handler, "Cannot delete a running job")
        return
    conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    conn.commit()
    _json_response(handler, {"ok": True, "job_id": job_id})


def _retry_job(handler, conn):
    body = _read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        _error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job.status not in (JobStatus.PARTIAL.value, JobStatus.FAILED.value):
        _error_response(handler, "Only partial or failed jobs can be retried")
        return
    try:
        next_job = repo.rerun(job.job_id)
    except ValueError as exc:
        _error_response(handler, str(exc))
        return
    _json_response(handler, {
        "ok": True,
        "job_id": next_job.job_id,
        "source_job_id": job.job_id,
        "status": next_job.status,
    })


def _pause_job(handler, conn):
    body = _read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        _error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job.status == JobStatus.PAUSED.value:
        _json_response(handler, {"ok": True, "job_id": job_id, "status": JobStatus.PAUSED.value})
        return
    if not repo.pause(job_id):
        _error_response(handler, "Cannot pause job")
        return
    _json_response(handler, {"ok": True, "job_id": job_id, "status": JobStatus.PAUSED.value})


def _resume_job(handler, conn):
    body = _read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        _error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job.status != JobStatus.PAUSED.value:
        _error_response(handler, "Only paused jobs can be resumed")
        return
    if not repo.resume(job_id):
        _error_response(handler, "Cannot resume job while it is still owned by a worker")
        return
    _json_response(handler, {"ok": True, "job_id": job_id, "status": JobStatus.QUEUED.value})


def _batch_delete_jobs(handler, conn):
    body = _read_json_body(handler)
    status_filter = body.get("status")
    if not status_filter:
        _error_response(handler, "status required")
        return
    if status_filter == "running":
        _error_response(handler, "Cannot batch delete running jobs")
        return
    rows = conn.execute("SELECT job_id FROM jobs WHERE status = ?", (status_filter,)).fetchall()
    job_ids = [r["job_id"] for r in rows]
    if not job_ids:
        _json_response(handler, {"ok": True, "deleted": 0})
        return
    for jid in job_ids:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (jid,))
    conn.execute("DELETE FROM jobs WHERE status = ?", (status_filter,))
    conn.commit()
    _json_response(handler, {"ok": True, "deleted": len(job_ids)})


def _batch_delete_jobs_by_ids(handler, conn):
    body = _read_json_body(handler)
    job_ids = body.get("job_ids", [])
    if not job_ids:
        _error_response(handler, "job_ids required")
        return
    placeholders = ",".join("?" for _ in job_ids)
    running = conn.execute(
        f"SELECT job_id FROM jobs WHERE job_id IN ({placeholders}) AND status = 'running'",
        job_ids,
    ).fetchall()
    if running:
        _error_response(handler, "Cannot delete running jobs")
        return
    for jid in job_ids:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (jid,))
    conn.execute(f"DELETE FROM jobs WHERE job_id IN ({placeholders})", job_ids)
    conn.commit()
    _json_response(handler, {"ok": True, "deleted": len(job_ids)})


def _safe_delete_job(handler, conn):
    body = _read_json_body(handler)
    job_id = body.get("job_id")
    if not job_id:
        _error_response(handler, "job_id required")
        return
    repo = JobsRepository(conn)
    job = repo.get(job_id)
    if job is None:
        _error_response(handler, "Job not found", HTTPStatus.NOT_FOUND)
        return
    if job.status == "running":
        ok = repo.request_cancel(job_id)
        if not ok:
            _error_response(handler, "Failed to request cancellation")
            return
        _json_response(handler, {"ok": True, "action": "cancel_requested", "job_id": job_id})
    else:
        conn.execute("DELETE FROM job_events WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        conn.commit()
        _json_response(handler, {"ok": True, "action": "deleted", "job_id": job_id})


def _threads_list(handler, params, conn):
    q = params.get("q", [""])[0].strip()
    forum_id = params.get("forum_id", [None])[0]
    days = params.get("days", [None])[0]
    repo = ThreadsRepository(conn)
    fid = int(forum_id) if forum_id else None
    rows = repo.search_threads(q, limit=200, forum_id=fid) if q else repo.list_threads(limit=200, forum_id=fid)
    result = [_thread_summary_dict(r) for r in rows]
    if days:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        result = [r for r in result if r.get("pub_time") and r["pub_time"][:10] >= cutoff_str]
    _json_response(handler, result)


def _load_thread_archive_metadata(settings: Settings, tid: int) -> dict:
    meta_path = settings.data_dir / "threads" / str(tid) / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _strip_anchor_tags(html: str) -> str:
    return re.sub(r"</?a\b[^>]*>", "", html, flags=re.IGNORECASE)


def _clean_rich_body_html(html: str | None) -> str | None:
    if not html:
        return None
    cleaned = _strip_anchor_tags(html)
    return normalize_rich_body_html(cleaned)


def _load_thread_archive_summary(settings: Settings, tid: int) -> dict:
    meta = _load_thread_archive_metadata(settings, tid)
    if not meta:
        return {}
    archive_summary = {
        "context_path": meta.get("context_path"),
        "archived_images": meta.get("archived_images") or {},
        "non_export_images": meta.get("non_export_images") or {},
        "shared_images": meta.get("shared_images") or {},
        "skipped_image_urls": meta.get("skipped_image_urls") or {},
        "missing_image_urls": meta.get("missing_image_urls") or [],
        "missing_shared_image_urls": meta.get("missing_shared_image_urls") or [],
    }
    return archive_summary


def _thread_detail(handler, tid, conn, params, settings):
    repo = ThreadsRepository(conn)
    thread = repo.get_thread(tid)
    if thread is None:
        _error_response(handler, "Thread not found", HTTPStatus.NOT_FOUND)
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
    data = _thread_summary_dict(thread)
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
    data["floors"] = [_floor_to_dict(f) for f in floors]
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
            rich_body_map[int(pid)] = _clean_rich_body_html(str(rich_body_html)) or ""
        except (TypeError, ValueError):
            continue
    if rich_body_map:
        for floor in data["floors"]:
            rich_body_html = rich_body_map.get(floor["pid"])
            if rich_body_html:
                floor["rich_body_html"] = rich_body_html
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
            data["missing_image_urls"] = json.loads(thread["missing_images_json"] or "[]")
        except ValueError:
            data["missing_image_urls"] = []
    else:
        data["missing_image_urls"] = []
    series_id = thread["series_id"] if "series_id" in thread.keys() else None
    if series_id:
        series_row = conn.execute("SELECT canonical_title FROM series WHERE series_id = ?", (series_id,)).fetchone()
        data["series_title"] = series_row["canonical_title"] if series_row else None
    else:
        data["series_title"] = None
    rag_row = conn.execute(
        """
        SELECT
          COUNT(*) AS chunk_count,
          SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
          SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
          SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count,
          MAX(indexed_at) AS last_indexed_at
        FROM rag_chunks
        WHERE tid = ?
        """,
        (tid,),
    ).fetchone()
    latest_rag_job = conn.execute(
        """
        SELECT job_id, status, stage, updated_at, created_at
        FROM jobs
        WHERE job_type = 'rag_index' AND tid = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (tid,),
    ).fetchone()
    data["rag_summary"] = {
        "enabled": bool(getattr(settings, "rag_enabled", False)),
        "chunk_count": int(rag_row["chunk_count"] or 0),
        "indexed_chunk_count": int(rag_row["indexed_chunk_count"] or 0),
        "pending_chunk_count": int(rag_row["pending_chunk_count"] or 0),
        "failed_chunk_count": int(rag_row["failed_chunk_count"] or 0),
        "last_indexed_at": rag_row["last_indexed_at"],
        "latest_job": None if latest_rag_job is None else {
            "job_id": latest_rag_job["job_id"],
            "status": latest_rag_job["status"],
            "stage": latest_rag_job["stage"],
            "updated_at": latest_rag_job["updated_at"],
            "created_at": latest_rag_job["created_at"],
        },
    }
    _json_response(handler, data)


def _thread_assets(handler, tid, conn):
    assets = AssetsRepository(conn).list_assets(tid)
    _json_response(handler, [_asset_to_dict(a) for a in assets])


def _thread_blocks(handler, tid, conn):
    blocks = ContentBlocksRepository(conn).list_blocks(tid)
    _json_response(handler, [_block_to_dict(b) for b in blocks])


def _thread_update_check(handler, tid: int, settings: Settings) -> None:
    result = check_thread_updates(tid=tid)
    if result.get("status") == "failed" and result.get("reason") == f"thread {tid} not found":
        _error_response(handler, "Thread not found", HTTPStatus.NOT_FOUND)
        return
    _json_response(handler, result)


def _series_list(handler, conn):
    rows = SeriesRepository(conn).list_series(limit=200)
    _json_response(handler, [_series_to_dict(r) for r in rows])


def _series_detail(handler, series_id, conn):
    repo = SeriesRepository(conn)
    series = repo.get_series(series_id)
    if series is None:
        _error_response(handler, "Series not found", HTTPStatus.NOT_FOUND)
        return
    threads = repo.list_threads_for_series(series_id)
    _json_response(handler, {
        "series": _series_to_dict(series),
        "threads": [_thread_summary_dict(r) for r in threads],
    })


def _delete_series(handler, conn, settings):
    body = _read_json_body(handler)
    series_id = body.get("series_id")
    if not series_id:
        _error_response(handler, "series_id required")
        return
    try:
        repo = SeriesRepository(conn)
        before, after = repo.delete_series(int(series_id))
        AuditEventsRepository(conn).record(
            actor="web", action="delete_series", target_type="series",
            target_id=str(series_id), before=before, after=after,
        )
        conn.commit()
        _json_response(handler, {"ok": True, "series_id": int(series_id)})
    except ValueError as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)


def _similar_series(handler, series_id, conn):
    """Find series with similar keys for merge recommendations."""
    repo = SeriesRepository(conn)
    target = repo.get_series(series_id)
    if target is None:
        _error_response(handler, "Series not found", HTTPStatus.NOT_FOUND)
        return
    target_key = (target["series_key"] or "").strip().lower()
    if not target_key:
        _json_response(handler, [])
        return
    all_series = repo.list_series(limit=500)
    similar = []
    for s in all_series:
        sid = int(s["series_id"])
        if sid == series_id:
            continue
        skey = (s["series_key"] or "").strip().lower()
        if not skey:
            continue
        # Simple similarity: shared prefix or one contains the other
        if (target_key.startswith(skey[:4]) or skey.startswith(target_key[:4])
                or target_key in skey or skey in target_key):
            similar.append(_series_to_dict(s))
    _json_response(handler, similar[:10])


def _forums_list(handler, conn, settings):
    cache = read_forum_size_cache(settings) or {}
    cache_forums = cache.get("forums") if isinstance(cache, dict) else {}
    size_map = cache_forums if isinstance(cache_forums, dict) else {}
    rows = conn.execute(
        "SELECT f.*, COUNT(t.tid) AS thread_count FROM forums f "
        "LEFT JOIN threads t ON t.forum_id = f.forum_id "
        "GROUP BY f.forum_id ORDER BY f.forum_id"
    ).fetchall()
    _json_response(handler, [
        {"forum_id": r["forum_id"], "name": r["name"], "name_en": r["name_en"] if "name_en" in r.keys() else None,
         "content_kind": r["content_kind"],
         "thread_count": r["thread_count"], "enabled": bool(r["enabled"]),
         "archive_size_bytes": size_map.get(str(r["forum_id"]), {}).get("archive_bytes") if isinstance(size_map.get(str(r["forum_id"])), dict) else None,
         "archive_size_updated_at": size_map.get(str(r["forum_id"]), {}).get("updated_at") if isinstance(size_map.get(str(r["forum_id"])), dict) else None}
        for r in rows
    ])


def _refresh_forum_size_cache(handler, conn, settings):
    payload = refresh_forum_size_cache(settings, conn)
    _json_response(handler, {
        "ok": True,
        "updated_at": payload.get("updated_at"),
        "forum_count": len(payload.get("forums", {})) if isinstance(payload.get("forums"), dict) else 0,
    })


def _exports_list(handler, conn):
    rows = ThreadsRepository(conn).list_exports(limit=200)
    _json_response(handler, [_thread_summary_dict(r) for r in rows])


def _fonts_list(handler, settings):
    font_root = settings.data_dir / "fonts"
    if not font_root.exists():
        _json_response(handler, [])
        return
    fonts = []
    for path in sorted(font_root.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".ttf", ".otf", ".woff", ".woff2"}:
            continue
        label = path.stem
        family = f"YamiboReading-{len(fonts)}"
        fonts.append({
            "name": path.name,
            "label": label,
            "family": family,
            "url": f"/fonts/{quote(path.name)}",
        })
    _json_response(handler, fonts)


def _rag_overview(handler, conn, settings):
    jobs_repo = JobsRepository(conn)
    meta = RagChunksRepository(conn).read_index_meta()
    counts_row = conn.execute(
        """
        SELECT
          COUNT(*) AS total_threads,
          SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN 1 ELSE 0 END) AS indexed_threads,
          SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) = 0 THEN 1 ELSE 0 END) AS unindexed_threads,
          SUM(CASE WHEN COALESCE(rag_stats.total_chunk_count, 0) > 0 THEN rag_stats.total_chunk_count ELSE 0 END) AS total_chunks,
          SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN rag_stats.indexed_chunk_count ELSE 0 END) AS indexed_chunks,
          SUM(CASE WHEN COALESCE(rag_stats.pending_chunk_count, 0) > 0 THEN rag_stats.pending_chunk_count ELSE 0 END) AS pending_chunks,
          SUM(CASE WHEN COALESCE(rag_stats.failed_chunk_count, 0) > 0 THEN rag_stats.failed_chunk_count ELSE 0 END) AS failed_chunks
        FROM threads t
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS total_chunk_count,
            SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
            SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
            SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count
          FROM rag_chunks
          GROUP BY tid
        ) rag_stats ON rag_stats.tid = t.tid
        """
    ).fetchone()
    thread_total = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    recent_jobs = [_job_to_dict(job, conn) for job in jobs_repo.list(limit=150) if job.job_type == "rag_index"][:12]
    forum_rows = conn.execute(
        """
        SELECT
          f.forum_id,
          f.name,
          f.name_en,
          f.content_kind,
          COUNT(DISTINCT t.tid) AS thread_count,
          SUM(CASE WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN 1 ELSE 0 END) AS indexed_thread_count,
          SUM(CASE WHEN COALESCE(rag_stats.total_chunk_count, 0) > 0 THEN rag_stats.total_chunk_count ELSE 0 END) AS chunk_count
        FROM forums f
        LEFT JOIN threads t ON t.forum_id = f.forum_id
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS total_chunk_count,
            SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count
          FROM rag_chunks
          GROUP BY tid
        ) rag_stats ON rag_stats.tid = t.tid
        GROUP BY f.forum_id, f.name, f.name_en, f.content_kind
        ORDER BY f.forum_id
        """
    ).fetchall()
    _json_response(handler, {
        "enabled": settings.rag_enabled,
        "config": {
            "embedding_provider": settings.rag_embedding_provider,
            "embedding_model": settings.rag_embedding_model,
            "embedding_dimensions": settings.rag_embedding_dimensions,
            "chunker_version": settings.rag_chunker_version,
            "min_chunk_chars": settings.rag_min_chunk_chars,
            "max_chunk_chars": settings.rag_max_chunk_chars,
            "hybrid_fts_candidates": settings.rag_hybrid_fts_candidates,
            "hybrid_vector_candidates": settings.rag_hybrid_vector_candidates,
        },
        "index_meta": meta,
        "counts": {
            "thread_total": thread_total,
            "indexed_threads": int(counts_row["indexed_threads"] or 0),
            "unindexed_threads": int(counts_row["unindexed_threads"] or 0),
            "total_chunks": int(counts_row["total_chunks"] or 0),
            "indexed_chunks": int(counts_row["indexed_chunks"] or 0),
            "pending_chunks": int(counts_row["pending_chunks"] or 0),
            "failed_chunks": int(counts_row["failed_chunks"] or 0),
        },
        "forum_breakdown": [
            {
                "forum_id": row["forum_id"],
                "name": row["name"],
                "name_en": row["name_en"],
                "content_kind": row["content_kind"],
                "thread_count": row["thread_count"],
                "indexed_thread_count": row["indexed_thread_count"],
                "chunk_count": row["chunk_count"],
            }
            for row in forum_rows
        ],
        "recent_jobs": recent_jobs,
    })


def _rag_threads(handler, params, conn):
    query = (params.get("q", [""])[0] or "").strip()
    forum_id = params.get("forum_id", [None])[0]
    index_state = (params.get("index_state", ["unindexed"])[0] or "unindexed").strip()
    rag_status = (params.get("rag_status", ["all"])[0] or "all").strip()
    try:
        page = max(int(params.get("page", ["1"])[0]), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(max(int(params.get("page_size", ["20"])[0]), 1), 50)
    except (TypeError, ValueError):
        page_size = 20
    filters = []
    args: list[object] = []
    if query:
        filters.append("(COALESCE(t.display_title, t.raw_title) LIKE ? OR COALESCE(t.publisher, '') LIKE ?)")
        like = f"%{query}%"
        args.extend([like, like])
    if forum_id not in {None, "", "all"}:
        filters.append("t.forum_id = ?")
        args.append(int(forum_id))
    if index_state == "indexed":
        filters.append("COALESCE(rag_stats.indexed_chunk_count, 0) > 0 AND COALESCE(rag_jobs.live_job_count, 0) = 0")
    elif index_state == "indexing":
        filters.append("COALESCE(rag_jobs.live_job_count, 0) > 0")
    elif index_state == "unindexed":
        filters.append("(COALESCE(rag_stats.indexed_chunk_count, 0) = 0 OR COALESCE(rag_jobs.live_job_count, 0) > 0)")
    if rag_status == "pending":
        filters.append("(COALESCE(rag_stats.total_chunk_count, 0) = 0 OR COALESCE(rag_stats.pending_chunk_count, 0) > 0)")
    elif rag_status == "failed":
        filters.append("COALESCE(rag_stats.failed_chunk_count, 0) > 0")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    total_count_row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_count
        FROM threads t
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS total_chunk_count,
            SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
            SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
            SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count,
            MAX(indexed_at) AS last_indexed_at
          FROM rag_chunks
          GROUP BY tid
        ) rag_stats ON rag_stats.tid = t.tid
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS live_job_count
          FROM jobs
          WHERE job_type = 'rag_index'
            AND status IN ('queued', 'running', 'retrying', 'cancel_requested', 'paused', 'interrupted')
          GROUP BY tid
        ) rag_jobs ON rag_jobs.tid = t.tid
        {where}
        """,
        args,
    ).fetchone()
    total_count = int(total_count_row["total_count"] or 0)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size
    order_clause = (
        "COALESCE(rag_stats.last_indexed_at, '') DESC, t.tid DESC"
        if index_state == "indexed"
        else "CASE WHEN COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 1 ELSE 0 END ASC, COALESCE(t.sync_time, '') DESC, t.tid DESC"
    )
    rows = conn.execute(
        f"""
        SELECT
          t.tid,
          t.raw_title,
          t.display_title,
          t.publisher,
          t.sync_time,
          t.archive_status,
          t.forum_id,
          t.content_kind,
          t.category,
          COALESCE(rag_stats.chunk_count, 0) AS rag_chunk_count,
          COALESCE(rag_stats.indexed_chunk_count, 0) AS rag_indexed_chunk_count,
          COALESCE(rag_stats.pending_chunk_count, 0) AS rag_pending_chunk_count,
          COALESCE(rag_stats.failed_chunk_count, 0) AS rag_failed_chunk_count,
          rag_stats.last_indexed_at AS rag_last_indexed_at,
          CASE
            WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 AND COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 'indexing'
            WHEN COALESCE(rag_stats.indexed_chunk_count, 0) > 0 THEN 'indexed'
            WHEN COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 'indexing'
            ELSE 'unindexed'
          END AS rag_index_state
        FROM threads t
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS chunk_count,
            SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
            SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
            SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count,
            MAX(indexed_at) AS last_indexed_at
          FROM rag_chunks
          GROUP BY tid
        ) rag_stats ON rag_stats.tid = t.tid
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS live_job_count
          FROM jobs
          WHERE job_type = 'rag_index'
            AND status IN ('queued', 'running', 'retrying', 'cancel_requested', 'paused', 'interrupted')
          GROUP BY tid
        ) rag_jobs ON rag_jobs.tid = t.tid
        {where}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        [*args, page_size, offset],
    ).fetchall()
    _json_response(handler, {
        "index_state": index_state,
        "rag_status": rag_status,
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "items": [
            {
                "tid": row["tid"],
                "raw_title": row["raw_title"],
                "display_title": row["display_title"],
                "publisher": row["publisher"],
                "sync_time": row["sync_time"],
                "archive_status": row["archive_status"],
                "forum_id": row["forum_id"],
                "content_kind": row["content_kind"],
                "category": row["category"],
                "rag_chunk_count": row["rag_chunk_count"],
                "rag_indexed_chunk_count": row["rag_indexed_chunk_count"],
                "rag_pending_chunk_count": row["rag_pending_chunk_count"],
                "rag_failed_chunk_count": row["rag_failed_chunk_count"],
                "rag_last_indexed_at": row["rag_last_indexed_at"],
                "rag_index_state": row["rag_index_state"],
            }
            for row in rows
        ],
    })


def _rag_index(handler):
    body = _read_json_body(handler)
    result = create_rag_index_job(
        tid=int(body["tid"]) if body.get("tid") not in {None, ""} else None,
        force=bool(body.get("force", False)),
        embedding_dimensions=int(body["embedding_dimensions"]) if body.get("embedding_dimensions") not in {None, ""} else None,
    )
    payload = to_wire(result)
    status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_REQUEST
    _json_response(handler, payload, status)


def _select_rag_thread_ids(
    conn,
    *,
    tids: list[int] | None = None,
    q: str = "",
    forum_id: int | None = None,
    index_state: str = "unindexed",
    rag_status: str = "all",
) -> list[int]:
    if tids:
        placeholders = ",".join("?" for _ in tids)
        rows = conn.execute(
            f"SELECT tid FROM threads WHERE tid IN ({placeholders}) ORDER BY tid DESC",
            tids,
        ).fetchall()
        return [int(row["tid"]) for row in rows]

    filters = []
    args: list[object] = []
    if q:
        like = f"%{q}%"
        filters.append("(COALESCE(t.display_title, t.raw_title) LIKE ? OR COALESCE(t.publisher, '') LIKE ?)")
        args.extend([like, like])
    if forum_id is not None:
        filters.append("t.forum_id = ?")
        args.append(forum_id)
    if index_state == "indexed":
        filters.append("COALESCE(rag_stats.indexed_chunk_count, 0) > 0 AND COALESCE(rag_jobs.live_job_count, 0) = 0")
    elif index_state == "indexing":
        filters.append("COALESCE(rag_jobs.live_job_count, 0) > 0")
    elif index_state == "unindexed":
        filters.append("(COALESCE(rag_stats.indexed_chunk_count, 0) = 0 OR COALESCE(rag_jobs.live_job_count, 0) > 0)")
    if rag_status == "pending":
        filters.append("(COALESCE(rag_stats.chunk_count, 0) = 0 OR COALESCE(rag_stats.pending_chunk_count, 0) > 0)")
    elif rag_status == "failed":
        filters.append("COALESCE(rag_stats.failed_chunk_count, 0) > 0")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = conn.execute(
        f"""
        SELECT t.tid
        FROM threads t
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS chunk_count,
            SUM(CASE WHEN embedding_status = 'indexed' THEN 1 ELSE 0 END) AS indexed_chunk_count,
            SUM(CASE WHEN embedding_status = 'pending' THEN 1 ELSE 0 END) AS pending_chunk_count,
            SUM(CASE WHEN embedding_status = 'failed' THEN 1 ELSE 0 END) AS failed_chunk_count
          FROM rag_chunks
          GROUP BY tid
        ) rag_stats ON rag_stats.tid = t.tid
        LEFT JOIN (
          SELECT
            tid,
            COUNT(*) AS live_job_count
          FROM jobs
          WHERE job_type = 'rag_index'
            AND status IN ('queued', 'running', 'retrying', 'cancel_requested', 'paused', 'interrupted')
          GROUP BY tid
        ) rag_jobs ON rag_jobs.tid = t.tid
        {where}
        ORDER BY CASE WHEN COALESCE(rag_jobs.live_job_count, 0) > 0 THEN 1 ELSE 0 END ASC, COALESCE(t.sync_time, '') DESC, t.tid DESC
        LIMIT 200
        """,
        args,
    ).fetchall()
    return [int(row["tid"]) for row in rows]


def _rag_index_batch(handler, conn):
    body = _read_json_body(handler)
    raw_tids = body.get("tids")
    tids: list[int] | None = None
    if isinstance(raw_tids, list):
        tids = [int(value) for value in raw_tids if value not in {None, ""}]
    q = str(body.get("q") or "").strip()
    forum_id = int(body["forum_id"]) if body.get("forum_id") not in {None, "", "all"} else None
    index_state = str(body.get("index_state") or "unindexed")
    rag_status = str(body.get("rag_status") or "all")
    force = bool(body.get("force", False))
    embedding_dimensions = int(body["embedding_dimensions"]) if body.get("embedding_dimensions") not in {None, ""} else None
    if tids is not None:
        result = create_rag_index_batch_jobs(
            tids=tids,
            force=force,
            embedding_dimensions=embedding_dimensions,
        )
        if not result.ok or result.data is None:
            payload = to_wire(result)
            status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_REQUEST
            _json_response(handler, payload, status)
            return
        _json_response(handler, {"ok": True, **result.data})
        return
    target_ids = _select_rag_thread_ids(conn, tids=tids, q=q, forum_id=forum_id, index_state=index_state, rag_status=rag_status)
    repo = JobsRepository(conn)
    created_job_ids: list[str] = []
    reused_job_ids: list[str] = []
    for tid in target_ids:
        existing = None if force else repo.find_live_job_for_thread(job_type=JobType.RAG_INDEX.value, tid=tid)
        if existing is not None:
            reused_job_ids.append(existing.job_id)
            continue
        job = repo.create(
            JobType.RAG_INDEX.value,
            tid=tid,
            payload={"force": force},
        )
        created_job_ids.append(job.job_id)
    _json_response(handler, {
        "ok": True,
        "target_count": len(target_ids),
        "created_count": len(created_job_ids),
        "reused_count": len(reused_job_ids),
        "created_job_ids": created_job_ids,
        "reused_job_ids": reused_job_ids,
        "tids": target_ids,
    })


def _archive_threads_batch(handler):
    body = _read_json_body(handler)
    raw_tids = body.get("tids")
    if not isinstance(raw_tids, list):
        _error_response(handler, "tids required")
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
        _json_response(handler, payload, status)
        return
    _json_response(handler, {"ok": True, **result.data})


def _rag_search(handler):
    body = _read_json_body(handler)
    result = search_archived_content(
        query=str(body.get("query") or ""),
        mode=str(body.get("mode") or "hybrid"),
        top_k=int(body.get("top_k") or 10),
        forum_id=int(body["forum_id"]) if body.get("forum_id") not in {None, "", "all"} else None,
        content_kind=str(body["content_kind"]) if body.get("content_kind") not in {None, "", "all"} else None,
        tid=int(body["tid"]) if body.get("tid") not in {None, ""} else None,
        series_id=int(body["series_id"]) if body.get("series_id") not in {None, ""} else None,
        floor_start=int(body["floor_start"]) if body.get("floor_start") not in {None, ""} else None,
        floor_end=int(body["floor_end"]) if body.get("floor_end") not in {None, ""} else None,
    )
    payload = to_wire(result)
    status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_REQUEST
    _json_response(handler, payload, status)


def _review_items(handler, conn):
    titles = ThreadsRepository(conn).list_title_review_items(limit=100)
    series = SeriesRepository(conn).list_series_review_items(limit=100)
    _json_response(handler, {
        "titles": [_thread_summary_dict(r) for r in titles],
        "series": [_series_to_dict(r) for r in series],
    })


def _confirm_series(handler, conn, settings):
    body = _read_json_body(handler)
    series_id = body.get("series_id")
    if not series_id:
        _error_response(handler, "series_id required")
        return
    try:
        before, after = SeriesRepository(conn).confirm_series_review(int(series_id))
        AuditEventsRepository(conn).record(
            actor="web", action="confirm_series_review", target_type="series",
            target_id=str(series_id), before=before, after=after,
        )
        conn.commit()
        _json_response(handler, {"ok": True, "series_id": int(series_id)})
    except ValueError as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def _merge_series(handler, conn, settings):
    body = _read_json_body(handler)
    source_id = body.get("source_series_id")
    target_id = body.get("target_series_id")
    if not source_id or not target_id:
        _error_response(handler, "source_series_id and target_series_id required")
        return
    try:
        before, after = SeriesRepository(conn).merge_series(int(source_id), int(target_id))
        AuditEventsRepository(conn).record(
            actor="web", action="merge_series", target_type="series",
            target_id=str(target_id), before=before, after=after,
        )
        conn.commit()
        _json_response(handler, {"ok": True, "target_series_id": int(target_id)})
    except ValueError as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)


def _confirm_title(handler, conn, settings):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    try:
        before, after = ThreadsRepository(conn).confirm_title_review(int(tid))
        AuditEventsRepository(conn).record(
            actor="web", action="confirm_title_review", target_type="thread",
            target_id=str(tid), before=before, after=after,
        )
        conn.commit()
        _json_response(handler, {"ok": True, "tid": int(tid)})
    except ValueError as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def _rebuild_series(handler, conn, settings):
    job = JobsRepository(conn).create("title_refine", payload={"mode": "rebuild_series"})
    _json_response(handler, {"ok": True, "job_id": job.job_id})


def _update_title(handler, conn, settings):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    try:
        repo = ThreadsRepository(conn)
        thread_row = repo.get_thread(int(tid))
        title_row = repo.get_title_parse(int(tid))
        if thread_row is None or title_row is None:
            _error_response(handler, "Thread not found", HTTPStatus.NOT_FOUND)
            return
        before, after = repo.update_title_review(
            int(tid),
            display_title=body.get("display_title") or thread_row["display_title"] or thread_row["raw_title"] or "",
            group_name=body.get("group_name") if body.get("group_name") is not None else (title_row["group_name"] if "group_name" in title_row.keys() else None),
            author_guess=body.get("author_guess") if body.get("author_guess") is not None else (title_row["author_guess"] if "author_guess" in title_row.keys() else None),
            core_title_guess=body.get("core_title_guess") or (title_row["core_title_guess"] if "core_title_guess" in title_row.keys() else thread_row["raw_title"] or ""),
            series_key=body.get("series_key") or (title_row["series_key"] if "series_key" in title_row.keys() else ""),
            title_aliases=body.get("title_aliases") if body.get("title_aliases") is not None else json.loads(title_row["title_aliases_json"] or "[]"),
            chapter_name=body.get("chapter_name") if body.get("chapter_name") is not None else (title_row["chapter_name"] if "chapter_name" in title_row.keys() else None),
            chapter_index=body.get("chapter_index") if body.get("chapter_index") is not None else (title_row["chapter_index"] if "chapter_index" in title_row.keys() else None),
            chapter_index_end=body.get("chapter_index_end") if body.get("chapter_index_end") is not None else (title_row["chapter_index_end"] if "chapter_index_end" in title_row.keys() else None),
            chapter_title=body.get("chapter_title") if body.get("chapter_title") is not None else (title_row["chapter_title"] if "chapter_title" in title_row.keys() else None),
            subtitle=body.get("subtitle") if body.get("subtitle") is not None else (title_row["subtitle"] if "subtitle" in title_row.keys() else None),
            tags=body.get("tags") if body.get("tags") is not None else json.loads(title_row["tags_json"] or "[]"),
            confidence=body.get("confidence") if body.get("confidence") is not None else (title_row["confidence"] if "confidence" in title_row.keys() else None),
            needs_review=False,
        )
        AuditEventsRepository(conn).record(
            actor="web", action="update_title_review", target_type="thread",
            target_id=str(tid), before=before, after=after,
        )
        conn.commit()
        title_after = after.get("title_parse") if isinstance(after, dict) else None
        if isinstance(title_after, dict):
            update_title_hints(
                settings,
                group_name=title_after.get("group_name"),
                author_guess=title_after.get("author_guess"),
            )
        _json_response(handler, {"ok": True, "tid": int(tid)})
    except ValueError as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)


def _update_chapter(handler, conn):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    chapter_name = body.get("chapter_name")
    chapter_index = body.get("chapter_index")
    author_guess = body.get("author_guess")
    group_name = body.get("group_name")
    conn.execute(
        "UPDATE title_parse SET chapter_name=?, chapter_index=?, author_guess=?, group_name=? WHERE tid=?",
        (chapter_name, chapter_index, author_guess, group_name, int(tid)),
    )
    conn.commit()
    _json_response(handler, {"ok": True, "tid": int(tid)})


def _update_series(handler, conn, settings):
    body = _read_json_body(handler)
    series_id = body.get("series_id")
    if not series_id:
        _error_response(handler, "series_id required")
        return
    try:
        repo = SeriesRepository(conn)
        series = repo.get_series(int(series_id))
        if series is None:
            _error_response(handler, "Series not found", HTTPStatus.NOT_FOUND)
            return
        before = dict(series)
        canonical_title = body.get("canonical_title")
        if canonical_title is None or canonical_title == "":
            canonical_title = series["canonical_title"]
        series_key = body.get("series_key")
        if series_key is None or series_key == "":
            series_key = series["series_key"]
        author_guess = body.get("author_guess")
        if author_guess is None or author_guess == "":
            author_guess = series["author_guess"]
        conn.execute(
            "UPDATE series SET canonical_title=?, series_key=?, author_guess=?, updated_at=CURRENT_TIMESTAMP WHERE series_id=?",
            (canonical_title, series_key, author_guess, int(series_id)),
        )
        AuditEventsRepository(conn).record(
            actor="web", action="update_series", target_type="series",
            target_id=str(series_id), before=before, after={"canonical_title": canonical_title, "series_key": series_key, "author_guess": author_guess},
        )
        conn.commit()
        _json_response(handler, {"ok": True, "series_id": int(series_id)})
    except Exception as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)


def _resync_thread(handler, conn):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    payload = {"tid": int(tid)}
    if body.get("forum_id") is not None:
        payload["forum_id"] = int(body["forum_id"])
    job = JobsRepository(conn).create("sync_thread", tid=int(tid), payload=payload)
    _json_response(handler, {"ok": True, "job_id": job.job_id})


def _resync_threads_batch(handler, conn):
    body = _read_json_body(handler)
    tids = body.get("tids", [])
    if not tids:
        _error_response(handler, "tids required")
        return
    base_url = body.get("base_url") or None
    result = create_thread_archive_batch_jobs(tids=[int(tid) for tid in tids], base_url=base_url)
    _json_response(handler, {"ok": True, **(result.data or {})})


def _update_thread(handler, conn):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    payload = {"tid": int(tid)}
    if body.get("base_url"):
        payload["base_url"] = body["base_url"]
    job = JobsRepository(conn).create("update_thread", tid=int(tid), payload=payload)
    _json_response(handler, {"ok": True, "job_id": job.job_id})


EXPORTABLE_FORUMS = {30, 55}  # 漫画区, 轻小说区


def _export_thread(handler, conn, settings):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    forum_id = body.get("forum_id")
    if forum_id is not None:
        forum_id = int(forum_id)
    else:
        row = conn.execute("SELECT forum_id FROM threads WHERE tid = ?", (int(tid),)).fetchone()
        forum_id = row["forum_id"] if row and row["forum_id"] is not None else None
    if forum_id is not None and forum_id not in EXPORTABLE_FORUMS:
        _error_response(handler, "仅漫画区和轻小说区的贴子支持导出")
        return
    strategy = body.get("strategy") or settings.export_default_strategy
    payload = {"tid": int(tid), "strategy": strategy}
    if forum_id is not None:
        payload["forum_id"] = forum_id
    job = JobsRepository(conn).create("export_thread", tid=int(tid), payload=payload)
    _json_response(handler, {"ok": True, "job_id": job.job_id})


def _delete_thread(handler, conn, settings):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    try:
        before, after, deleted_series = _delete_thread_record(conn, int(tid))
        AuditEventsRepository(conn).record(
            actor="web", action="delete_thread", target_type="thread",
            target_id=str(tid), before=before, after=after,
        )
        conn.commit()
        resp: dict = {"ok": True, "tid": int(tid)}
        if deleted_series is not None:
            resp["deleted_series_id"] = deleted_series
        _json_response(handler, resp)
    except ValueError as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def _batch_delete_threads(handler, conn, settings):
    body = _read_json_body(handler)
    tids = body.get("tids", [])
    if not tids:
        _error_response(handler, "tids required")
        return
    try:
        deleted = 0
        deleted_series_ids: list[int] = []
        audit_repo = AuditEventsRepository(conn)
        unique_tids = list(dict.fromkeys(int(tid) for tid in tids))
        for tid in unique_tids:
            before, after, deleted_series = _delete_thread_record(conn, tid)
            audit_repo.record(
                actor="web", action="delete_thread", target_type="thread",
                target_id=str(tid), before=before, after=after,
            )
            deleted += 1
            if deleted_series is not None:
                deleted_series_ids.append(int(deleted_series))
        conn.commit()
        _json_response(handler, {
            "ok": True,
            "deleted": deleted,
            "tids": unique_tids,
            "deleted_series_ids": deleted_series_ids,
        })
    except ValueError as exc:
        conn.rollback()
        _error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def _delete_thread_record(conn, tid: int) -> tuple[dict[str, object], dict[str, object], int | None]:
    repo = ThreadsRepository(conn)
    before, after = repo.delete_thread(tid)
    deleted_series = None
    series_id = before.get("series_id")
    if series_id:
        remaining = conn.execute("SELECT COUNT(*) FROM threads WHERE series_id = ?", (series_id,)).fetchone()[0]
        if remaining == 0:
            conn.execute("DELETE FROM series WHERE series_id = ?", (series_id,))
            deleted_series = int(series_id)
    return before, after, deleted_series


def _thread_images(handler, tid, conn, settings):
    """Read images from metadata.json since assets table may be empty."""
    import json as _json
    from pathlib import Path
    meta_path = settings.data_dir / "threads" / str(tid) / "metadata.json"
    if not meta_path.exists():
        _json_response(handler, [])
        return
    try:
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        _json_response(handler, [])
        return
    images = []
    floors = meta.get("floors", [])
    for floor in floors:
        pid = floor.get("pid")
        # Combine all image URL lists
        for key in ("content_image_urls", "image_urls", "shared_image_urls", "skipped_image_urls"):
            urls = floor.get(key) or []
            for url in urls:
                if not isinstance(url, str) or not url.strip():
                    continue
                # Skip duplicates
                if any(img["url"] == url for img in images):
                    continue
                images.append({
                    "pid": pid,
                    "url": url,
                    "source": key,
                    "is_content": key == "content_image_urls",
                    "is_shared": key == "shared_image_urls",
                })
    _json_response(handler, images)


def _debug_info(handler, conn, settings):
    import platform
    from pathlib import Path
    thread_count = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    series_count = conn.execute("SELECT COUNT(*) FROM series").fetchone()[0]
    job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]
    asset_count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    block_count = conn.execute("SELECT COUNT(*) FROM content_blocks").fetchone()[0]
    forum_count = conn.execute("SELECT COUNT(*) FROM forums").fetchone()[0]
    recent_jobs = conn.execute(
        "SELECT job_id, job_type, status, created_at FROM jobs ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    recent_errors = conn.execute(
        "SELECT job_id, error_code, error_message, finished_at FROM jobs WHERE error_code IS NOT NULL ORDER BY finished_at DESC LIMIT 10"
    ).fetchall()
    static_dir = Path(__file__).parent / "static"
    _json_response(handler, {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "data_dir": str(settings.data_dir),
        "db_path": str(settings.db_path),
        "static_exists": static_dir.exists(),
        "stats": {
            "threads": thread_count,
            "series": series_count,
            "jobs": job_count,
            "events": event_count,
            "assets": asset_count,
            "blocks": block_count,
            "forums": forum_count,
        },
        "recent_jobs": [
            {"job_id": r["job_id"], "type": r["job_type"], "status": r["status"], "created": r["created_at"]}
            for r in recent_jobs
        ],
        "recent_errors": [
            {"job_id": r["job_id"], "code": r["error_code"], "message": r["error_message"], "finished": r["finished_at"]}
            for r in recent_errors
        ],
    })


def _logs(handler, params):
    from yamibo_mcp.web.log_buffer import get_log_buffer
    limit_str = params.get("limit", ["200"])[0]
    since_str = params.get("since", [None])[0]
    limit = min(int(limit_str), 500)
    since_ts = float(since_str) if since_str else None
    buf = get_log_buffer()
    entries = buf.get_recent(limit=limit, since_ts=since_ts)
    _json_response(handler, {"entries": entries, "count": len(entries)})


# ─── Dict converters ───

_JOB_TYPE_LABELS = {
    "sync_thread": "同步贴子",
    "update_thread": "追加更新贴子",
    "export_thread": "导出贴子",
    "rag_index": "构建 RAG 索引",
    "title_refine": "重算标题/系列",
    "cleanup_job": "清理任务",
    "noop": "空任务",
}

_EXPORT_STRATEGY_LABELS = {
    "cache_only": "仅缓存",
    "sync_if_stale": "过期则同步",
    "force_resync": "强制重同步",
}

_JOB_TYPE_LABELS_EN = {
    "sync_thread": "Sync thread",
    "update_thread": "Append update thread",
    "export_thread": "Export thread",
    "rag_index": "Build RAG index",
    "title_refine": "Rebuild titles/series",
    "cleanup_job": "Cleanup",
    "noop": "Noop",
}

_EXPORT_STRATEGY_LABELS_EN = {
    "cache_only": "cache only",
    "sync_if_stale": "sync if stale",
    "force_resync": "force resync",
}


def _describe_job(conn, job) -> str:
    payload = job.payload if isinstance(job.payload, dict) else {}
    tid = payload.get("tid") or job.tid

    thread_title = ""
    if tid:
        row = conn.execute("SELECT raw_title FROM threads WHERE tid = ?", (tid,)).fetchone()
        if row:
            thread_title = row["raw_title"] or ""

    def _short():
        if not thread_title:
            return ""
        return (thread_title[:30] + "...") if len(thread_title) > 30 else thread_title

    if job.job_type == "sync_thread":
        desc = "同步贴子"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        return desc

    if job.job_type == "update_thread":
        desc = "追加更新贴子"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        return desc

    if job.job_type == "export_thread":
        strategy = payload.get("strategy", "")
        strategy_label = _EXPORT_STRATEGY_LABELS.get(strategy, strategy)
        desc = "导出贴子"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        if strategy_label:
            desc += f"（策略：{strategy_label}）"
        return desc

    if job.job_type == "rag_index":
        desc = "构建 RAG 索引"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        return desc

    if job.job_type == "title_refine":
        mode = payload.get("mode", "")
        if mode == "rebuild_series":
            return "批量重算系列"
        return "重算标题/系列"

    if job.job_type == "cleanup_job":
        mode = payload.get("mode", "")
        return f"清理：{mode}" if mode else "清理任务"

    return _JOB_TYPE_LABELS.get(job.job_type, job.job_type)


def _describe_job_en(conn, job) -> str:
    payload = job.payload if isinstance(job.payload, dict) else {}
    tid = payload.get("tid") or job.tid

    thread_title = ""
    if tid:
        row = conn.execute("SELECT raw_title FROM threads WHERE tid = ?", (tid,)).fetchone()
        if row:
            thread_title = row["raw_title"] or ""

    def _short():
        if not thread_title:
            return ""
        return (thread_title[:30] + "...") if len(thread_title) > 30 else thread_title

    if job.job_type == "sync_thread":
        desc = "Sync thread"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        return desc

    if job.job_type == "update_thread":
        desc = "Append update thread"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        return desc

    if job.job_type == "export_thread":
        strategy = payload.get("strategy", "")
        strategy_label = _EXPORT_STRATEGY_LABELS_EN.get(strategy, strategy)
        desc = "Export thread"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        if strategy_label:
            desc += f" ({strategy_label})"
        return desc

    if job.job_type == "rag_index":
        desc = "Build RAG index"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        return desc

    if job.job_type == "title_refine":
        mode = payload.get("mode", "")
        if mode == "rebuild_series":
            return "Batch rebuild series"
        return "Rebuild titles/series"

    if job.job_type == "cleanup_job":
        mode = payload.get("mode", "")
        return f"Cleanup: {mode}" if mode else "Cleanup"

    return _JOB_TYPE_LABELS_EN.get(job.job_type, job.job_type)


def _job_to_dict(job, conn=None) -> dict:
    payload = job.payload if isinstance(job.payload, dict) else {}
    artifacts = job.artifacts if isinstance(job.artifacts, dict) else {}
    description = _describe_job(conn, job) if conn else job.job_type
    description_en = _describe_job_en(conn, job) if conn else job.job_type
    return {
        "job_id": job.job_id, "job_type": job.job_type, "status": job.status,
        "stage": job.stage, "tid": job.tid,
        "description": description, "description_en": description_en,
        "payload": payload, "artifacts": artifacts,
        "progress_current": job.progress_current, "progress_total": job.progress_total,
        "worker_id": job.worker_id, "error_code": job.error_code,
        "error_message": job.error_message, "paused_at": getattr(job, "paused_at", None), "created_at": job.created_at,
        "updated_at": job.updated_at, "finished_at": job.finished_at,
    }


def _event_to_dict(e) -> dict:
    return {
        "event_id": e.event_id, "job_id": e.job_id, "event_type": e.event_type,
        "status": e.status, "stage": e.stage, "payload": e.payload, "created_at": e.created_at,
    }


def _thread_summary_dict(row) -> dict:
    def g(k):
        return row[k] if k in row.keys() else None
    return {
        "tid": int(row["tid"]), "raw_title": row["raw_title"],
        "display_title": row["display_title"] or row["raw_title"],
        "publisher": g("publisher"), "pub_time": g("pub_time"), "sync_time": g("sync_time"),
        "archive_status": g("archive_status"), "validation_status": g("validation_status"),
        "context_path": g("context_path"), "series_id": g("series_id"),
        "export_path": g("export_path"), "forum_id": g("forum_id"),
        "content_kind": g("content_kind"), "core_title_guess": g("core_title_guess"),
        "series_key": g("series_key"), "chapter_name": g("chapter_name"),
        "category": g("category"), "reply_count": g("reply_count") or 0,
    }


def _floor_to_dict(row) -> dict:
    rich_body_html = row["rich_body_html"] if "rich_body_html" in row.keys() else None
    return {
        "pid": row["pid"], "floor_no": row["floor_no"],
        "publisher": row["publisher"], "content": row["content"] or "",
        "pub_time": row["pub_time"], "has_images": bool(row["has_images"]),
        "publisher_uid": row["publisher_uid"] if "publisher_uid" in row.keys() else None,
        "quote_text": row["quote_text"] if "quote_text" in row.keys() else None,
        "reply_text": row["reply_text"] if "reply_text" in row.keys() else None,
        "rich_body_html": _clean_rich_body_html(rich_body_html),
    }


def _asset_to_dict(row) -> dict:
    return {
        "asset_id": row["asset_id"], "tid": row["tid"], "pid": row["pid"],
        "asset_type": row["asset_type"], "remote_url": row["remote_url"],
        "local_path": row["local_path"], "exportable": bool(row["exportable"]),
        "required": bool(row["required"]), "status": row["status"],
    }


def _block_to_dict(row) -> dict:
    return {
        "id": row["id"], "tid": row["tid"], "pid": row["pid"],
        "order_index": row["order_index"], "block_type": row["block_type"],
        "text": row["text"], "asset_id": row["asset_id"],
    }


def _series_to_dict(row) -> dict:
    def g(k):
        return row[k] if k in row.keys() else None
    return {
        "series_id": int(row["series_id"]), "canonical_title": g("canonical_title"),
        "series_key": g("series_key"), "author_guess": g("author_guess"),
        "thread_count": g("thread_count"), "needs_review": g("needs_review"),
        "last_sync_time": g("last_sync_time"), "aliases_json": g("aliases_json"),
    }


def _audit_to_dict(row) -> dict:
    return {
        "event_id": str(row["event_id"]), "actor": row["actor"],
        "action": row["action"], "target_type": row["target_type"],
        "target_id": row["target_id"], "created_at": row["created_at"],
        "description": _describe_audit(row),
        "description_en": _describe_audit_en(row),
    }


_AUDIT_ACTION_LABELS = {
    "delete_series": "删除系列",
    "confirm_series_review": "确认系列",
    "merge_series": "合并系列",
    "confirm_title_review": "确认标题",
    "update_title_review": "更新标题",
    "update_series": "更新系列",
    "delete_thread": "删除贴子",
}

_AUDIT_TARGET_LABELS = {
    "series": "系列",
    "thread": "贴子",
}

_AUDIT_ACTION_LABELS_EN = {
    "delete_series": "Delete series",
    "confirm_series_review": "Confirm series",
    "merge_series": "Merge series",
    "confirm_title_review": "Confirm title",
    "update_title_review": "Update title",
    "update_series": "Update series",
    "delete_thread": "Delete thread",
}

_AUDIT_TARGET_LABELS_EN = {
    "series": "series",
    "thread": "thread",
}


def _describe_audit(row) -> str:
    action = row["action"]
    target_type = row["target_type"]
    target_id = row["target_id"]
    action_label = _AUDIT_ACTION_LABELS.get(action, action)
    target_label = _AUDIT_TARGET_LABELS.get(target_type, target_type)

    if target_type == "thread" and target_id and target_id.isdigit():
        conn = row._connection if hasattr(row, '_connection') else None
        title = ""
        if conn:
            r = conn.execute("SELECT raw_title FROM threads WHERE tid = ?", (int(target_id),)).fetchone()
            if r:
                title = r["raw_title"] or ""
        short = (title[:20] + "...") if len(title) > 20 else title
        if short:
            return f"{action_label}：{target_label} #{target_id}「{short}」"
        return f"{action_label}：{target_label} #{target_id}"

    if target_type == "series" and target_id and target_id.isdigit():
        return f"{action_label}：{target_label} #{target_id}"

    return f"{action_label}：{target_label} {target_id}"


def _describe_audit_en(row) -> str:
    action = row["action"]
    target_type = row["target_type"]
    target_id = row["target_id"]
    action_label = _AUDIT_ACTION_LABELS_EN.get(action, action)
    target_label = _AUDIT_TARGET_LABELS_EN.get(target_type, target_type)

    if target_type == "thread" and target_id and target_id.isdigit():
        conn = row._connection if hasattr(row, '_connection') else None
        title = ""
        if conn:
            r = conn.execute("SELECT raw_title FROM threads WHERE tid = ?", (int(target_id),)).fetchone()
            if r:
                title = r["raw_title"] or ""
        short = (title[:20] + "...") if len(title) > 20 else title
        if short:
            return f"{action_label} {target_label} #{target_id} \"{short}\""
        return f"{action_label} {target_label} #{target_id}"

    if target_type == "series" and target_id and target_id.isdigit():
        return f"{action_label} {target_label} #{target_id}"

    return f"{action_label} {target_label} {target_id}"


def _worker_heartbeats(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT worker_id, MAX(heartbeat_at) AS latest_heartbeat_at, "
        "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_jobs, "
        "COUNT(*) AS seen_jobs "
        "FROM jobs WHERE worker_id IS NOT NULL GROUP BY worker_id "
        "HAVING SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) > 0 "
        "ORDER BY COALESCE(MAX(heartbeat_at), MAX(updated_at)) DESC LIMIT 20"
    ).fetchall()
    return [{"worker_id": r["worker_id"], "running_jobs": r["running_jobs"],
             "seen_jobs": r["seen_jobs"], "latest_heartbeat_at": r["latest_heartbeat_at"]} for r in rows]
