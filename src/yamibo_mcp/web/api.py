from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.config import Settings
from yamibo_mcp.services.title_hints import update_title_hints


def _json_response(handler, data, status=HTTPStatus.OK):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status.value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


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
    elif route.startswith("/jobs/") and route.endswith("/events"):
        job_id = route[6:-7]
        _job_events(handler, job_id, conn)
    elif route.startswith("/jobs/") and handler.command == "GET":
        job_id = route[6:]
        _job_detail(handler, job_id, conn)
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
    elif route.startswith("/threads/") and handler.command == "GET":
        tid = int(route[9:])
        _thread_detail(handler, tid, conn)
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
        _forums_list(handler, conn)
    elif route == "/exports" and handler.command == "GET":
        _exports_list(handler, conn)
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
    elif route == "/review/update-series" and handler.command == "POST":
        _update_series(handler, conn, settings)
    elif route == "/threads/resync" and handler.command == "POST":
        _resync_thread(handler, conn)
    elif route == "/threads/export" and handler.command == "POST":
        _export_thread(handler, conn, settings)
    elif route == "/threads/delete" and handler.command == "POST":
        _delete_thread(handler, conn, settings)
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
    recent_jobs = [_job_to_dict(j) for j in jobs_repo.list(limit=10)]
    workers = _worker_heartbeats(conn)
    audits = [_audit_to_dict(r) for r in AuditEventsRepository(conn).list_recent(limit=8)]
    recent_threads = [_thread_summary_dict(r) for r in threads_repo.list_threads(limit=recent_limit)]

    _json_response(handler, {
        "thread_count": thread_count,
        "series_count": series_count,
        "export_count": export_count,
        "forum_counts": forum_counts,
        "recent_jobs": recent_jobs,
        "workers": workers,
        "recent_audits": audits,
        "recent_threads": recent_threads,
    })


def _jobs_list(handler, params, conn):
    status = params.get("status", [None])[0]
    jobs = JobsRepository(conn).list(limit=150, status=status)
    _json_response(handler, [_job_to_dict(j) for j in jobs])


def _job_detail(handler, job_id, conn):
    job = JobsRepository(conn).get(job_id)
    _json_response(handler, _job_to_dict(job))


def _job_events(handler, job_id, conn):
    events = JobEventsRepository(conn).list(job_id=job_id, limit=200)
    _json_response(handler, [_event_to_dict(e) for e in events])


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


def _thread_detail(handler, tid, conn):
    repo = ThreadsRepository(conn)
    thread = repo.get_thread(tid)
    if thread is None:
        _error_response(handler, "Thread not found", HTTPStatus.NOT_FOUND)
        return
    floors = repo.list_floors(tid)
    data = _thread_summary_dict(thread)
    data["url"] = f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}"
    data["floors"] = [_floor_to_dict(f) for f in floors]
    data["publisher_uid"] = thread["publisher_uid"] if "publisher_uid" in thread.keys() else None
    data["pub_time"] = thread["pub_time"] if "pub_time" in thread.keys() else None
    data["image_count"] = thread["image_count"] if "image_count" in thread.keys() else 0
    data["primary_media_type"] = thread["primary_media_type"] if "primary_media_type" in thread.keys() else None
    _json_response(handler, data)


def _thread_assets(handler, tid, conn):
    assets = AssetsRepository(conn).list_assets(tid)
    _json_response(handler, [_asset_to_dict(a) for a in assets])


def _thread_blocks(handler, tid, conn):
    blocks = ContentBlocksRepository(conn).list_blocks(tid)
    _json_response(handler, [_block_to_dict(b) for b in blocks])


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


def _forums_list(handler, conn):
    rows = conn.execute(
        "SELECT f.*, COUNT(t.tid) AS thread_count FROM forums f "
        "LEFT JOIN threads t ON t.forum_id = f.forum_id "
        "GROUP BY f.forum_id ORDER BY f.forum_id"
    ).fetchall()
    _json_response(handler, [
        {"forum_id": r["forum_id"], "name": r["name"], "name_en": r["name_en"] if "name_en" in r.keys() else None,
         "content_kind": r["content_kind"],
         "thread_count": r["thread_count"], "enabled": bool(r["enabled"])}
        for r in rows
    ])


def _exports_list(handler, conn):
    rows = ThreadsRepository(conn).list_exports(limit=200)
    _json_response(handler, [_thread_summary_dict(r) for r in rows])


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
        before, after = repo.update_title_review(
            int(tid),
            display_title=body.get("display_title", ""),
            group_name=body.get("group_name"),
            author_guess=body.get("author_guess"),
            core_title_guess=body.get("core_title_guess", ""),
            series_key=body.get("series_key", ""),
            title_aliases=body.get("title_aliases"),
            chapter_name=body.get("chapter_name"),
            chapter_index=body.get("chapter_index"),
            chapter_index_end=body.get("chapter_index_end"),
            chapter_title=body.get("chapter_title"),
            subtitle=body.get("subtitle"),
            tags=body.get("tags"),
            confidence=body.get("confidence"),
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


def _export_thread(handler, conn, settings):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    strategy = body.get("strategy") or settings.export_default_strategy
    payload = {"tid": int(tid), "strategy": strategy}
    if body.get("forum_id") is not None:
        payload["forum_id"] = int(body["forum_id"])
    job = JobsRepository(conn).create("export_thread", tid=int(tid), payload=payload)
    _json_response(handler, {"ok": True, "job_id": job.job_id})


def _delete_thread(handler, conn, settings):
    body = _read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        _error_response(handler, "tid required")
        return
    try:
        repo = ThreadsRepository(conn)
        before, after = repo.delete_thread(int(tid))
        deleted_series = None
        series_id = before.get("series_id")
        if series_id:
            remaining = conn.execute("SELECT COUNT(*) FROM threads WHERE series_id = ?", (series_id,)).fetchone()[0]
            if remaining == 0:
                conn.execute("DELETE FROM series WHERE series_id = ?", (series_id,))
                deleted_series = series_id
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

def _job_to_dict(job) -> dict:
    return {
        "job_id": job.job_id, "job_type": job.job_type, "status": job.status,
        "stage": job.stage, "tid": job.tid,
        "progress_current": job.progress_current, "progress_total": job.progress_total,
        "worker_id": job.worker_id, "error_code": job.error_code,
        "error_message": job.error_message, "created_at": job.created_at,
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
        "category": g("category"),
    }


def _floor_to_dict(row) -> dict:
    return {
        "pid": row["pid"], "floor_no": row["floor_no"],
        "publisher": row["publisher"], "content": row["content"] or "",
        "pub_time": row["pub_time"], "has_images": bool(row["has_images"]),
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
    }


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
