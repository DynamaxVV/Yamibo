from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.web_fastapi.converters import series_to_dict, thread_summary_dict
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings

router = APIRouter(prefix="/api", tags=["series"])


@router.get("/series")
def list_series(conn: DatabaseConnection = Depends(get_conn)):
    rows = SeriesRepository(conn).list_series(limit=200)
    return [series_to_dict(r) for r in rows]


@router.get("/series/{series_id}")
def series_detail(series_id: int, conn: DatabaseConnection = Depends(get_conn)):
    repo = SeriesRepository(conn)
    series = repo.get_series(series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="Series not found")
    threads = repo.list_threads_for_series(series_id)
    return {
        "series": series_to_dict(series),
        "threads": [thread_summary_dict(r) for r in threads],
    }


@router.post("/series/delete")
def delete_series(body: dict, conn: DatabaseConnection = Depends(get_conn)):
    series_id = body.get("series_id")
    if not series_id:
        raise HTTPException(status_code=400, detail="series_id required")
    try:
        repo = SeriesRepository(conn)
        before, after = repo.delete_series(int(series_id))
        AuditEventsRepository(conn).record(
            actor="web", action="delete_series", target_type="series",
            target_id=str(series_id), before=before, after=after,
        )
        conn.commit()
        return {"ok": True, "series_id": int(series_id)}
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/series/{series_id}/similar")
def similar_series(series_id: int, conn: DatabaseConnection = Depends(get_conn)):
    repo = SeriesRepository(conn)
    target = repo.get_series(series_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Series not found")
    target_key = (target["series_key"] or "").strip().lower()
    if not target_key:
        return []
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
    return similar[:10]
