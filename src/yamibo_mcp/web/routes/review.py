from __future__ import annotations

from http import HTTPStatus

from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.services.title_hints import update_title_hints
from ._helpers import json_response, error_response, read_json_body, jsonish_loads
from ._converters import thread_summary_dict, series_to_dict


def handle_review_items(handler, conn):
    titles = ThreadsRepository(conn).list_title_review_items(limit=100)
    series = SeriesRepository(conn).list_series_review_items(limit=100)
    json_response(handler, {
        "titles": [thread_summary_dict(r) for r in titles],
        "series": [series_to_dict(r) for r in series],
    })


def handle_confirm_series(handler, conn, settings):
    body = read_json_body(handler)
    series_id = body.get("series_id")
    if not series_id:
        error_response(handler, "series_id required")
        return
    try:
        before, after = SeriesRepository(conn).confirm_series_review(int(series_id))
        AuditEventsRepository(conn).record(
            actor="web", action="confirm_series_review", target_type="series",
            target_id=str(series_id), before=before, after=after,
        )
        conn.commit()
        json_response(handler, {"ok": True, "series_id": int(series_id)})
    except ValueError as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def handle_merge_series(handler, conn, settings):
    body = read_json_body(handler)
    source_id = body.get("source_series_id")
    target_id = body.get("target_series_id")
    if not source_id or not target_id:
        error_response(handler, "source_series_id and target_series_id required")
        return
    try:
        before, after = SeriesRepository(conn).merge_series(int(source_id), int(target_id))
        AuditEventsRepository(conn).record(
            actor="web", action="merge_series", target_type="series",
            target_id=str(target_id), before=before, after=after,
        )
        conn.commit()
        json_response(handler, {"ok": True, "target_series_id": int(target_id)})
    except ValueError as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)


def handle_confirm_title(handler, conn, settings):
    body = read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        error_response(handler, "tid required")
        return
    try:
        before, after = ThreadsRepository(conn).confirm_title_review(int(tid))
        AuditEventsRepository(conn).record(
            actor="web", action="confirm_title_review", target_type="thread",
            target_id=str(tid), before=before, after=after,
        )
        conn.commit()
        json_response(handler, {"ok": True, "tid": int(tid)})
    except ValueError as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.NOT_FOUND)


def handle_rebuild_series(handler, conn, settings):
    job = JobsRepository(conn).create("title_refine", payload={"mode": "rebuild_series"})
    json_response(handler, {"ok": True, "job_id": job.job_id})


def handle_update_title(handler, conn, settings):
    body = read_json_body(handler)
    tid = body.get("tid")
    if not tid:
        error_response(handler, "tid required")
        return
    try:
        repo = ThreadsRepository(conn)
        thread_row = repo.get_thread(int(tid))
        title_row = repo.get_title_parse(int(tid))
        if thread_row is None or title_row is None:
            error_response(handler, "Thread not found", HTTPStatus.NOT_FOUND)
            return
        before, after = repo.update_title_review(
            int(tid),
            display_title=body.get("display_title") or thread_row["display_title"] or thread_row["raw_title"] or "",
            group_name=body.get("group_name") if body.get("group_name") is not None else (title_row["group_name"] if "group_name" in title_row.keys() else None),
            author_guess=body.get("author_guess") if body.get("author_guess") is not None else (title_row["author_guess"] if "author_guess" in title_row.keys() else None),
            core_title_guess=body.get("core_title_guess") or (title_row["core_title_guess"] if "core_title_guess" in title_row.keys() else thread_row["raw_title"] or ""),
            series_key=body.get("series_key") or (title_row["series_key"] if "series_key" in title_row.keys() else ""),
            title_aliases=body.get("title_aliases") if body.get("title_aliases") is not None else jsonish_loads(title_row["title_aliases_json"], []),
            chapter_name=body.get("chapter_name") if body.get("chapter_name") is not None else (title_row["chapter_name"] if "chapter_name" in title_row.keys() else None),
            chapter_index=body.get("chapter_index") if body.get("chapter_index") is not None else (title_row["chapter_index"] if "chapter_index" in title_row.keys() else None),
            chapter_index_end=body.get("chapter_index_end") if body.get("chapter_index_end") is not None else (title_row["chapter_index_end"] if "chapter_index_end" in title_row.keys() else None),
            chapter_title=body.get("chapter_title") if body.get("chapter_title") is not None else (title_row["chapter_title"] if "chapter_title" in title_row.keys() else None),
            subtitle=body.get("subtitle") if body.get("subtitle") is not None else (title_row["subtitle"] if "subtitle" in title_row.keys() else None),
            tags=body.get("tags") if body.get("tags") is not None else jsonish_loads(title_row["tags_json"], []),
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
        json_response(handler, {"ok": True, "tid": int(tid)})
    except ValueError as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)


def handle_update_series(handler, conn, settings):
    body = read_json_body(handler)
    series_id = body.get("series_id")
    if not series_id:
        error_response(handler, "series_id required")
        return
    try:
        repo = SeriesRepository(conn)
        series = repo.get_series(int(series_id))
        if series is None:
            error_response(handler, "Series not found", HTTPStatus.NOT_FOUND)
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
        SeriesRepository(conn).update_series_metadata(
            int(series_id),
            canonical_title=canonical_title,
            series_key=series_key,
            author_guess=author_guess,
        )
        AuditEventsRepository(conn).record(
            actor="web", action="update_series", target_type="series",
            target_id=str(series_id), before=before, after={"canonical_title": canonical_title, "series_key": series_key, "author_guess": author_guess},
        )
        conn.commit()
        json_response(handler, {"ok": True, "series_id": int(series_id)})
    except Exception as exc:
        conn.rollback()
        error_response(handler, str(exc), HTTPStatus.BAD_REQUEST)
