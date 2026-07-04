from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.services.title_hints import update_title_hints
from yamibo_mcp.web_fastapi.converters import thread_summary_dict, series_to_dict
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings
from yamibo_mcp.web_fastapi.helpers import jsonish_loads

router = APIRouter(prefix="/api", tags=["review"])


@router.get("/review")
def review_items(conn: DatabaseConnection = Depends(get_conn)):
    titles = ThreadsRepository(conn).list_title_review_items(limit=100)
    series = SeriesRepository(conn).list_series_review_items(limit=100)
    return {
        "titles": [thread_summary_dict(r) for r in titles],
        "series": [series_to_dict(r) for r in series],
    }


@router.post("/review/confirm-series")
def confirm_series(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    series_id = body.get("series_id")
    if not series_id:
        raise HTTPException(status_code=400, detail="series_id required")
    try:
        before, after = SeriesRepository(conn).confirm_series_review(int(series_id))
        AuditEventsRepository(conn).record(
            actor="web", action="confirm_series_review", target_type="series",
            target_id=str(series_id), before=before, after=after,
        )
        conn.commit()
        return {"ok": True, "series_id": int(series_id)}
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/review/merge-series")
def merge_series(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    source_id = body.get("source_series_id")
    target_id = body.get("target_series_id")
    if not source_id or not target_id:
        raise HTTPException(status_code=400, detail="source_series_id and target_series_id required")
    try:
        before, after = SeriesRepository(conn).merge_series(int(source_id), int(target_id))
        AuditEventsRepository(conn).record(
            actor="web", action="merge_series", target_type="series",
            target_id=str(target_id), before=before, after=after,
        )
        conn.commit()
        return {"ok": True, "target_series_id": int(target_id)}
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/review/confirm-title")
def confirm_title(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    tid = body.get("tid")
    if not tid:
        raise HTTPException(status_code=400, detail="tid required")
    try:
        before, after = ThreadsRepository(conn).confirm_title_review(int(tid))
        AuditEventsRepository(conn).record(
            actor="web", action="confirm_title_review", target_type="thread",
            target_id=str(tid), before=before, after=after,
        )
        conn.commit()
        return {"ok": True, "tid": int(tid)}
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/review/rebuild-series")
def rebuild_series(conn: DatabaseConnection = Depends(get_conn)):
    job = JobsRepository(conn).create("title_refine", payload={"mode": "rebuild_series"})
    return {"ok": True, "job_id": job.job_id}


@router.post("/review/update-title")
def update_title(body: dict, conn: DatabaseConnection = Depends(get_conn), settings=Depends(get_settings)):
    tid = body.get("tid")
    if not tid:
        raise HTTPException(status_code=400, detail="tid required")
    try:
        repo = ThreadsRepository(conn)
        thread_row = repo.get_thread(int(tid))
        title_row = repo.get_title_parse(int(tid))
        if thread_row is None or title_row is None:
            raise HTTPException(status_code=404, detail="Thread not found")
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
        return {"ok": True, "tid": int(tid)}
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/review/update-series")
def update_series(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    series_id = body.get("series_id")
    if not series_id:
        raise HTTPException(status_code=400, detail="series_id required")
    try:
        repo = SeriesRepository(conn)
        series = repo.get_series(int(series_id))
        if series is None:
            raise HTTPException(status_code=404, detail="Series not found")
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
            target_id=str(series_id), before=before,
            after={"canonical_title": canonical_title, "series_key": series_key, "author_guess": author_guess},
        )
        conn.commit()
        return {"ok": True, "series_id": int(series_id)}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
