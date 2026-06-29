from __future__ import annotations

from http import HTTPStatus

from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from ._helpers import json_response, error_response, read_json_body
from ._converters import series_to_dict, thread_summary_dict


def handle_series_list(handler, conn):
    rows = SeriesRepository(conn).list_series(limit=200)
    json_response(handler, [series_to_dict(r) for r in rows])


def handle_series_detail(handler, series_id, conn):
    repo = SeriesRepository(conn)
    series = repo.get_series(series_id)
    if series is None:
        error_response(handler, "Series not found", HTTPStatus.NOT_FOUND)
        return
    threads = repo.list_threads_for_series(series_id)
    json_response(handler, {
        "series": series_to_dict(series),
        "threads": [thread_summary_dict(r) for r in threads],
    })


def handle_delete_series(handler, conn, settings):
    body = read_json_body(handler)
    series_id = body.get("series_id")
    if not series_id:
        error_response(handler, "series_id required")
        return
    try:
        repo = SeriesRepository(conn)
        before, after = repo.delete_series(int(series_id))
        AuditEventsRepository(conn).record(
            actor="web", action="delete_series", target_type="series",
            target_id=str(series_id), before=before, after=after,
        )
        conn.commit()
        json_response(handler, {"ok": True, "series_id": int(series_id)})
    except ValueError as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)


def handle_similar_series(handler, series_id, conn):
    """Find series with similar keys for merge recommendations."""
    repo = SeriesRepository(conn)
    target = repo.get_series(series_id)
    if target is None:
        error_response(handler, "Series not found", HTTPStatus.NOT_FOUND)
        return
    target_key = (target["series_key"] or "").strip().lower()
    if not target_key:
        json_response(handler, [])
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
        if (target_key.startswith(skey[:4]) or skey.startswith(target_key[:4])
                or target_key in skey or skey in target_key):
            similar.append(series_to_dict(s))
    json_response(handler, similar[:10])
