from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.application.rag_commands import create_rag_index_batch_jobs, create_rag_index_job
from yamibo_mcp.application.rag_queries import search_archived_content
from yamibo_mcp.server.agent_adapter import to_wire
from yamibo_mcp.web_fastapi.converters import job_rows_to_dicts
from yamibo_mcp.web_fastapi.deps import get_conn, get_settings

router = APIRouter(prefix="/api", tags=["rag"])


@router.get("/rag/overview")
def rag_overview(conn: DatabaseConnection = Depends(get_conn), settings=Depends(get_settings)):
    jobs_repo = JobsRepository(conn)
    rag_repo = RagChunksRepository(conn)
    meta = rag_repo.read_index_meta()
    counts_row = rag_repo.count_rag_overview()
    coverage_diagnostics = rag_repo.count_floor_coverage_diagnostics()
    thread_total = counts_row["total_threads"]
    recent_jobs = job_rows_to_dicts(
        [job for job in jobs_repo.list(limit=150) if job.job_type == "rag_index"][:12],
        conn, include_details=False,
    )
    forum_rows = rag_repo.list_rag_forum_breakdown()
    return {
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
            "indexed_threads": counts_row["indexed_threads"],
            "unindexed_threads": counts_row["unindexed_threads"],
            "total_chunks": counts_row["total_chunks"],
            "indexed_chunks": counts_row["indexed_chunks"],
            "pending_chunks": counts_row["pending_chunks"],
            "failed_chunks": counts_row["failed_chunks"],
        },
        "coverage_diagnostics": coverage_diagnostics,
        "forum_breakdown": [
            {
                "forum_id": row["forum_id"], "name": row["name"],
                "name_en": row["name_en"], "content_kind": row["content_kind"],
                "thread_count": row["thread_count"],
                "indexed_thread_count": row["indexed_thread_count"],
                "chunk_count": row["chunk_count"],
            }
            for row in forum_rows
        ],
        "recent_jobs": recent_jobs,
    }


@router.get("/rag/threads")
def rag_threads(
    q: str = "",
    forum_id: int | None = None,
    index_state: str = "unindexed",
    rag_status: str = "all",
    page: int = 1,
    page_size: int = 20,
    conn: DatabaseConnection = Depends(get_conn),
):
    payload = RagChunksRepository(conn).list_rag_threads(
        q=q.strip() or None,
        forum_id=forum_id,
        page=page, page_size=min(max(page_size, 1), 50),
        index_state=index_state, rag_status=rag_status,
    )
    return {
        "index_state": index_state, "rag_status": rag_status,
        "page": payload["page"], "page_size": payload["page_size"],
        "total_count": payload["total_count"], "total_pages": payload["total_pages"],
        "items": [
            {
                "tid": row["tid"], "raw_title": row["raw_title"],
                "display_title": row["display_title"], "publisher": row["publisher"],
                "sync_time": row["sync_time"], "archive_status": row["archive_status"],
                "forum_id": row["forum_id"], "content_kind": row["content_kind"],
                "category": row["category"], "rag_chunk_count": row["rag_chunk_count"],
                "rag_indexed_chunk_count": row["rag_indexed_chunk_count"],
                "rag_pending_chunk_count": row["rag_pending_chunk_count"],
                "rag_failed_chunk_count": row["rag_failed_chunk_count"],
                "rag_last_indexed_at": row["rag_last_indexed_at"],
                "rag_index_state": row["rag_index_state"],
            }
            for row in payload["rows"]
        ],
    }


@router.post("/rag/index")
def rag_index(body: dict):
    result = create_rag_index_job(
        tid=int(body["tid"]) if body.get("tid") not in {None, ""} else None,
        force=bool(body.get("force", False)),
        embedding_dimensions=int(body["embedding_dimensions"]) if body.get("embedding_dimensions") not in {None, ""} else None,
    )
    return to_wire(result)


@router.post("/rag/index-batch")
def rag_index_batch(body: dict, conn: DatabaseConnection = Depends(get_conn)):
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
        result = create_rag_index_batch_jobs(tids=tids, force=force, embedding_dimensions=embedding_dimensions)
        if not result.ok or result.data is None:
            return to_wire(result)
        return {"ok": True, **result.data}

    target_ids = RagChunksRepository(conn).select_rag_thread_ids(
        tids=tids, q=q, forum_id=forum_id,
        index_state=index_state, rag_status=rag_status,
    )
    repo = JobsRepository(conn)
    created_job_ids: list[str] = []
    reused_job_ids: list[str] = []
    for tid in target_ids:
        existing = None if force else repo.find_live_job_for_thread(job_type=JobType.RAG_INDEX.value, tid=tid)
        if existing is not None:
            reused_job_ids.append(existing.job_id)
            continue
        job = repo.create(JobType.RAG_INDEX.value, tid=tid, payload={"force": force})
        created_job_ids.append(job.job_id)
    return {
        "ok": True,
        "target_count": len(target_ids),
        "created_count": len(created_job_ids),
        "reused_count": len(reused_job_ids),
        "created_job_ids": created_job_ids,
        "reused_job_ids": reused_job_ids,
        "tids": target_ids,
    }


@router.post("/rag/search")
def rag_search(body: dict):
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
    return to_wire(result)
