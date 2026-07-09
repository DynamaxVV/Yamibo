from __future__ import annotations

import hashlib
import json
import logging

from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.rag_vectors import RagVectorUnavailableError, get_vector_repository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.rag.anime_dry_run import build_anime_dry_run_thread
from yamibo_mcp.rag.anime_materializer import MATERIALIZER_VERSION, _build_thread_materialization, _write_thread_artifacts
from yamibo_mcp.rag.chunker import build_rag_chunks, build_rag_chunks_from_preview_rows
from yamibo_mcp.rag.embeddings import build_embedding_provider
from yamibo_mcp.storage.paths import StoragePaths


LOG = logging.getLogger(__name__)


def handle_rag_index(repo, job, worker_id: str, lease_seconds: int, settings) -> None:
    if job.tid is None:
        repo.fail(job.job_id, "RAG_TID_REQUIRED", "rag_index job requires tid")
        return

    repo.update_stage(job.job_id, "collect", progress_current=1, progress_total=6)
    threads_repo = ThreadsRepository(repo.conn)
    thread_row = threads_repo.get_thread(job.tid)
    if thread_row is None:
        _debug_rag_index(
            settings,
            "local archive missing",
            job_id=job.job_id,
            tid=job.tid,
            stage="collect",
        )
        repo.fail(job.job_id, "LOCAL_ARCHIVE_NOT_FOUND", f"Thread {job.tid} is not archived locally")
        return
    title_row = threads_repo.get_title_parse(job.tid)
    floor_rows = threads_repo.list_floors(job.tid)

    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    repo.update_stage(job.job_id, "chunk", progress_current=2, progress_total=6)
    chunks = _load_indexable_chunks(
        settings=settings,
        thread_row=thread_row,
        title_row=title_row,
        floor_rows=floor_rows,
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
        warning = f"embedding failed: {exc}"
        _debug_rag_index(
            settings,
            warning,
            job_id=job.job_id,
            tid=job.tid,
            stage="embed",
            chunk_count=len(chunk_rows),
            embedding_model=getattr(provider, "model", None),
            embedding_dimensions=getattr(provider, "dimensions", None),
            chunk_preview=_chunk_preview(chunk_rows),
            exc_type=exc.__class__.__name__,
        )
        repo.partial(
            job.job_id,
            artifacts={"tid": job.tid, "warning": warning, "chunk_count": len(chunk_rows)},
        )
        return
    if embeddings:
        embedding_dimensions = len(embeddings[0])
        if any(len(embedding) != embedding_dimensions for embedding in embeddings):
            chunks_repo.set_embedding_status([str(row["chunk_id"]) for row in chunk_rows], status="failed")
            _debug_rag_index(
                settings,
                "embedding failed: inconsistent embedding dimensions",
                job_id=job.job_id,
                tid=job.tid,
                stage="embed",
                chunk_count=len(chunk_rows),
                embedding_model=getattr(provider, "model", None),
                embedding_dimensions=getattr(provider, "dimensions", None),
                chunk_preview=_chunk_preview(chunk_rows),
            )
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
        vectors_repo = get_vector_repository(repo.conn)
        vector_backend_version = vectors_repo.reset_if_dimensions_changed(
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
                "vector_backend_version": vector_backend_version,
            }
        )
    except RagVectorUnavailableError as exc:
        chunks_repo.set_embedding_status([str(row["chunk_id"]) for row in chunk_rows], status="failed")
        warning = str(exc)
        _debug_rag_index(
            settings,
            warning,
            job_id=job.job_id,
            tid=job.tid,
            stage="vec",
            chunk_count=len(chunk_rows),
            embedding_model=getattr(provider, "model", None),
            embedding_dimensions=embedding_dimensions,
            chunk_preview=_chunk_preview(chunk_rows),
            exc_type=exc.__class__.__name__,
        )
        repo.partial(job.job_id, artifacts={"tid": job.tid, "warning": warning, "chunk_count": len(chunk_rows)})
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


def _debug_rag_index(settings, message: str, **context) -> None:
    if not getattr(settings, "rag_debug_indexing", False):
        return
    parts = [f"{key}={value!r}" for key, value in context.items() if value is not None]
    LOG.warning("[RAG-DEBUG] %s %s", message, " ".join(parts))


def _load_indexable_chunks(*, settings, thread_row, title_row, floor_rows):
    paths = StoragePaths(settings.data_dir)
    result = build_anime_dry_run_thread(
        thread_row=thread_row,
        floor_rows=floor_rows,
        structured_cleaning=True,
    )
    materialized = _build_thread_materialization(result=result, thread_row=thread_row, version=MATERIALIZER_VERSION)
    _write_thread_artifacts(paths=paths, tid=int(thread_row["tid"]), materialized=materialized)
    preview_rows = _read_materialized_preview_rows(paths=paths, tid=int(thread_row["tid"]), version=MATERIALIZER_VERSION)
    if preview_rows:
        return build_rag_chunks_from_preview_rows(
            thread_row=thread_row,
            title_row=title_row,
            preview_rows=preview_rows,
        )
    return build_rag_chunks(
        thread_row=thread_row,
        title_row=title_row,
        floor_rows=floor_rows,
        settings=settings,
    )


def _read_materialized_preview_rows(*, paths: StoragePaths, tid: int, version: str) -> list[dict]:
    marker_path = paths.thread_rag_materialized_marker(tid, version)
    preview_path = paths.thread_rag_chunks_preview_jsonl(tid, version)
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if not marker.get("complete"):
        raise ValueError(f"incomplete rag materialization marker for tid={tid}")
    artifacts = marker.get("artifacts") or {}
    preview_meta = artifacts.get(preview_path.name) or {}
    preview_content = preview_path.read_text(encoding="utf-8")
    expected_hash = preview_meta.get("sha256")
    if expected_hash and _sha256_text(preview_content) != expected_hash:
        raise ValueError(f"rag preview hash mismatch for tid={tid}")
    return [json.loads(line) for line in preview_content.splitlines() if line.strip()]


def _chunk_preview(chunk_rows, *, limit: int = 2, text_limit: int = 160) -> list[dict[str, object]]:
    preview: list[dict[str, object]] = []
    for row in chunk_rows[:limit]:
        text = str(row["text"] or "")
        preview.append(
            {
                "chunk_id": row["chunk_id"],
                "pid": row.get("pid") if hasattr(row, "get") else row["pid"] if "pid" in row.keys() else None,
                "floor_no": row.get("floor_no") if hasattr(row, "get") else row["floor_no"] if "floor_no" in row.keys() else None,
                "text_preview": text[:text_limit] + ("..." if len(text) > text_limit else ""),
            }
        )
    return preview


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
