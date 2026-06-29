from __future__ import annotations

from yamibo_mcp.application.contracts import AgentAction, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.resource_uris import job_events_uri


def create_rag_index_job(
    *,
    tid: int | None = None,
    force: bool = False,
    embedding_dimensions: int | None = None,
) -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        repo = JobsRepository(conn)
        if tid is not None and not force:
            existing = repo.find_live_job_for_thread(job_type=JobType.RAG_INDEX.value, tid=tid)
            if existing is not None:
                return AgentResult(
                    ok=True,
                    data={
                        "job_id": existing.job_id,
                        "tid": tid,
                        "created": False,
                        "job_type": JobType.RAG_INDEX.value,
                    },
                    resources={"job_events": job_events_uri(existing.job_id)},
                    next_actions=[
                        AgentAction(tool="read_job", args={"job_id": existing.job_id}, reason="Poll the queued RAG indexing job."),
                    ],
                    side_effects=["sqlite_job_reused"],
                )
        job = repo.create(
            JobType.RAG_INDEX.value,
            tid=tid,
            payload={
                "force": force,
                "embedding_dimensions": embedding_dimensions or settings.rag_embedding_dimensions,
            },
        )
        return AgentResult(
            ok=True,
            data={"job_id": job.job_id, "tid": tid, "created": True, "job_type": JobType.RAG_INDEX.value},
            resources={"job_events": job_events_uri(job.job_id)},
            next_actions=[
                AgentAction(tool="read_job", args={"job_id": job.job_id}, reason="Poll the queued RAG indexing job."),
            ],
            side_effects=["sqlite_job_created"],
        )
    finally:
        conn.close()


def create_rag_index_batch_jobs(
    *,
    tids: list[int],
    force: bool = False,
    embedding_dimensions: int | None = None,
) -> AgentResult:
    normalized_tids = list(dict.fromkeys(int(tid) for tid in tids if tid))
    if not normalized_tids:
        raise ValueError("tids required")
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        repo = JobsRepository(conn)
        created_job_ids: list[str] = []
        reused_job_ids: list[str] = []
        for tid in normalized_tids:
            if not force:
                existing = repo.find_live_job_for_thread(job_type=JobType.RAG_INDEX.value, tid=tid)
                if existing is not None:
                    reused_job_ids.append(existing.job_id)
                    continue
            job = repo.create(
                JobType.RAG_INDEX.value,
                tid=tid,
                payload={
                    "force": force,
                    "embedding_dimensions": embedding_dimensions or settings.rag_embedding_dimensions,
                },
            )
            created_job_ids.append(job.job_id)
        return AgentResult(
            ok=True,
            data={
                "job_type": JobType.RAG_INDEX.value,
                "target_count": len(normalized_tids),
                "created_count": len(created_job_ids),
                "reused_count": len(reused_job_ids),
                "created_job_ids": created_job_ids,
                "reused_job_ids": reused_job_ids,
                "tids": normalized_tids,
            },
            side_effects=["sqlite_job_created" if created_job_ids else "sqlite_job_reused"],
        )
    finally:
        conn.close()
