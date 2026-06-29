from __future__ import annotations

from typing import Literal

from yamibo_mcp.application.contracts import AgentError, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.rag_vectors import RagVectorUnavailableError, get_vector_repository
from yamibo_mcp.rag.embeddings import build_embedding_provider
from yamibo_mcp.rag.scoring import hybrid_score, metadata_score, normalize_keyword_rank, normalize_vector_distance
from yamibo_mcp.services.llm_client import LLMRequestError


SEARCH_MODES = {"hybrid", "keyword", "vector"}
SNIPPET_MAX_LEN = 220


def search_archived_content(
    *,
    query: str,
    mode: str = "hybrid",
    top_k: int = 10,
    forum_id: int | None = None,
    content_kind: str | None = None,
    tid: int | None = None,
    series_id: int | None = None,
    floor_start: int | None = None,
    floor_end: int | None = None,
) -> AgentResult:
    if mode not in SEARCH_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    settings = load_settings()
    if not settings.rag_enabled:
        return AgentResult(ok=False, error=AgentError(code="RAG_DISABLED", message="RAG is disabled in settings."))
    conn = connect(settings.db_path)
    try:
        chunks_repo = RagChunksRepository(conn)
        keyword_rows = []
        vector_rows = []

        if mode in {"keyword", "hybrid"}:
            keyword_rows = chunks_repo.keyword_search(
                query=query,
                top_k=settings.rag_hybrid_fts_candidates if mode == "hybrid" else top_k,
                forum_id=forum_id,
                content_kind=content_kind,
                tid=tid,
                series_id=series_id,
                floor_start=floor_start,
                floor_end=floor_end,
            )

        if mode in {"vector", "hybrid"}:
            try:
                provider = build_embedding_provider(settings)
                query_embedding = provider.embed_texts([query])[0]
                vector_rows = get_vector_repository(conn).search(
                    query_embedding=query_embedding,
                    top_k=settings.rag_hybrid_vector_candidates if mode == "hybrid" else top_k,
                    forum_id=forum_id,
                    content_kind=content_kind,
                    tid=tid,
                    series_id=series_id,
                    floor_start=floor_start,
                    floor_end=floor_end,
                )
            except (RagVectorUnavailableError, LLMRequestError, OSError, TimeoutError, IndexError) as exc:
                if mode == "vector":
                    return AgentResult(
                        ok=False,
                        error=AgentError(code="RAG_VECTOR_UNAVAILABLE", message=str(exc)),
                    )

        items = _merge_results(
            keyword_rows=keyword_rows,
            vector_rows=vector_rows,
            top_k=top_k,
            query=query,
            vector_metric="cosine" if conn.backend in {"postgres", "postgresql"} else "legacy",
            filters={
                "tid": tid,
                "series_id": series_id,
                "forum_id": forum_id,
                "content_kind": content_kind,
            },
        )
        return AgentResult(
            ok=True,
            data={
                "query": query,
                "mode": mode,
                "top_k": top_k,
                "count": len(items),
                "items": items,
            },
        )
    finally:
        conn.close()


def _merge_results(*, keyword_rows: list, vector_rows: list, top_k: int, query: str, vector_metric: Literal["legacy", "cosine"], filters: dict[str, object | None]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for rank, row in enumerate(keyword_rows):
        entry = merged.setdefault(str(row["chunk_id"]), _base_item(row, query))
        entry["score_parts"]["keyword"] = normalize_keyword_rank(rank)
    for row in vector_rows:
        entry = merged.setdefault(str(row["chunk_id"]), _base_item(row, query))
        entry["score_parts"]["vector"] = normalize_vector_distance(float(row["vector_distance"]), metric=vector_metric)
    for entry in merged.values():
        entry["score_parts"]["metadata"] = metadata_score(
            tid_filter=filters["tid"],
            series_id_filter=filters["series_id"],
            forum_id_filter=filters["forum_id"],
            content_kind_filter=filters["content_kind"],
            row_tid=int(entry["tid"]),
            row_series_id=entry["series_id"],
            row_forum_id=entry["forum_id"],
            row_content_kind=entry["content_kind"],
            floor_no=entry["floor_no"],
        )
        entry["score"] = hybrid_score(
            keyword=float(entry["score_parts"]["keyword"]),
            vector=float(entry["score_parts"]["vector"]),
            metadata=float(entry["score_parts"]["metadata"]),
        )
    items = sorted(merged.values(), key=lambda item: float(item["score"]), reverse=True)[:top_k]
    for item in items:
        item.pop("series_id", None)
        item.pop("forum_id", None)
    return items


def _base_item(row, query: str) -> dict[str, object]:
    text = str(row["text"] or "")
    return {
        "chunk_id": row["chunk_id"],
        "tid": row["tid"],
        "pid": row["pid"],
        "floor_no": row["floor_no"],
        "display_title": row["title"] or "",
        "publisher": row["publisher"],
        "pub_time": row["pub_time"],
        "content_kind": row["content_kind"],
        "snippet": _build_snippet(text, query),
        "score": 0.0,
        "score_parts": {"keyword": 0.0, "vector": 0.0, "metadata": 0.0},
        "source_uri": row["source_uri"],
        "series_id": row["series_id"],
        "forum_id": row["forum_id"],
    }


def _build_snippet(text: str, query: str, *, max_len: int = SNIPPET_MAX_LEN) -> str:
    if len(text) <= max_len:
        return text

    match_start = _find_best_match(text, query)
    if match_start is None:
        return text[:max_len]

    start = max(0, match_start - max_len // 3)
    end = min(len(text), start + max_len)
    if end - start < max_len:
        start = max(0, end - max_len)
    snippet = text[start:end]
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(text):
        snippet = f"{snippet}..."
    return snippet


def _find_best_match(text: str, query: str) -> int | None:
    candidates = [query.strip()]
    candidates.extend(term.strip() for term in query.replace("，", " ").replace("。", " ").replace("、", " ").split())
    for candidate in candidates:
        if not candidate:
            continue
        idx = text.find(candidate)
        if idx != -1:
            return idx
    return None
