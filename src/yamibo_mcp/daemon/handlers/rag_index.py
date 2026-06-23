from __future__ import annotations

from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.rag_vectors import RagVectorUnavailableError, RagVectorsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.rag.chunker import build_rag_chunks
from yamibo_mcp.rag.embeddings import build_embedding_provider


def handle_rag_index(repo, job, worker_id: str, lease_seconds: int, settings) -> None:
    if job.tid is None:
        repo.fail(job.job_id, "RAG_TID_REQUIRED", "rag_index job requires tid")
        return

    repo.update_stage(job.job_id, "collect", progress_current=1, progress_total=6)
    threads_repo = ThreadsRepository(repo.conn)
    thread_row = threads_repo.get_thread(job.tid)
    if thread_row is None:
        repo.fail(job.job_id, "LOCAL_ARCHIVE_NOT_FOUND", f"Thread {job.tid} is not archived locally")
        return
    title_row = threads_repo.get_title_parse(job.tid)
    floor_rows = threads_repo.list_floors(job.tid)

    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    repo.update_stage(job.job_id, "chunk", progress_current=2, progress_total=6)
    chunks = build_rag_chunks(
        thread_row=thread_row,
        title_row=title_row,
        floor_rows=floor_rows,
        settings=settings,
    )
    chunks_repo = RagChunksRepository(repo.conn)
    chunk_rows = chunks_repo.replace_thread_chunks(
        tid=job.tid,
        chunks=chunks,
        embedding_model=settings.rag_embedding_model,
        embedding_dimensions=int(job.payload.get("embedding_dimensions") or settings.rag_embedding_dimensions),
    )

    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    repo.update_stage(job.job_id, "fts", progress_current=3, progress_total=6)

    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    repo.update_stage(job.job_id, "embed", progress_current=4, progress_total=6)
    provider = build_embedding_provider(settings)
    try:
        embeddings = provider.embed_texts([str(row["text"]) for row in chunk_rows])
    except Exception as exc:
        chunks_repo.set_embedding_status([str(row["chunk_id"]) for row in chunk_rows], status="failed")
        repo.partial(job.job_id, artifacts={"tid": job.tid, "warning": f"embedding failed: {exc}", "chunk_count": len(chunk_rows)})
        return
    if embeddings:
        embedding_dimensions = len(embeddings[0])
        if any(len(embedding) != embedding_dimensions for embedding in embeddings):
            chunks_repo.set_embedding_status([str(row["chunk_id"]) for row in chunk_rows], status="failed")
            repo.partial(
                job.job_id,
                artifacts={
                    "tid": job.tid,
                    "warning": "embedding failed: inconsistent embedding dimensions",
                    "chunk_count": len(chunk_rows),
                },
            )
            return
    else:
        embedding_dimensions = int(job.payload.get("embedding_dimensions") or settings.rag_embedding_dimensions)

    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    repo.update_stage(job.job_id, "vec", progress_current=5, progress_total=6)
    try:
        vectors_repo = RagVectorsRepository(repo.conn)
        sqlite_vec_version = vectors_repo.reset_if_dimensions_changed(
            dimensions=embedding_dimensions
        )
        vectors_repo.delete_thread_embeddings([int(row["id"]) for row in chunk_rows])
        vectors_repo.replace_embeddings(
            [(int(row["id"]), embedding) for row, embedding in zip(chunk_rows, embeddings, strict=True)]
        )
        chunks_repo.set_embedding_status(
            [str(row["chunk_id"]) for row in chunk_rows],
            status="indexed",
            embedding_model=provider.model,
            embedding_dimensions=provider.dimensions,
        )
        chunks_repo.write_index_meta(
            {
                "embedding_model": provider.model,
                "embedding_dimensions": str(embedding_dimensions),
                "embedding_provider": settings.rag_embedding_provider,
                "chunker_version": settings.rag_chunker_version,
                "sqlite_vec_version": sqlite_vec_version,
            }
        )
    except RagVectorUnavailableError as exc:
        chunks_repo.set_embedding_status([str(row["chunk_id"]) for row in chunk_rows], status="failed")
        repo.partial(job.job_id, artifacts={"tid": job.tid, "warning": str(exc), "chunk_count": len(chunk_rows)})
        return

    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    repo.update_stage(job.job_id, "verify", progress_current=6, progress_total=6)
    repo.succeed(
        job.job_id,
        artifacts={
            "tid": job.tid,
            "chunk_count": len(chunk_rows),
            "embedding_model": provider.model,
            "embedding_dimensions": embedding_dimensions,
        },
    )
