"""Discussion trend V1 query API.

Step 04 — read-only queries against the current trend mart run.
All functions return AgentResult. None create jobs or mutate state.
"""

from __future__ import annotations

import logging
from typing import Any

from yamibo_mcp.application.contracts import AgentError, AgentResult
from yamibo_mcp.application.discussion_trend_support import (
    aggregate_topic_rows,
    aggregate_user_rows,
    merge_rag_search_rows,
)
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.discussion_trends import DiscussionTrendRepository
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.rag_vectors import RagVectorUnavailableError, get_vector_repository
from yamibo_mcp.rag.embeddings import build_embedding_provider
from yamibo_mcp.rag.scoring import metadata_score
from yamibo_mcp.services.llm_client import LLMRequestError
from yamibo_mcp.structured_logging import emit

LOG = logging.getLogger(__name__)

DEFAULT_VERSION = "trend-v1"
MIN_TOPICS = 2
RAG_SQL_FALLBACK_WARNING = "DISCUSSION_RAG_SQL_FALLBACK_USED"

VALID_GRANULARITIES = {"day", "month"}
VALID_SORT_BY = {"post_count", "thread_count", "topic_count"}
VALID_EVIDENCE_MODES = {"auto", "sql", "rag"}
VALID_EVIDENCE_INTENTS = {
    "general_research",
    "slang_usage",
    "community_atmosphere",
    "topic_investigation",
    "trend_context",
}


def _open_repo():
    settings = load_settings()
    conn = connect(settings.db_path)
    repo = DiscussionTrendRepository(conn)
    return conn, repo


def _resolve_current_run(
    repo: DiscussionTrendRepository,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str,
) -> tuple[Any, dict[str, Any] | None]:
    """Return (run, error_dict) — caller returns early if error_dict is set."""
    run = repo.get_current_run(
        forum_id=forum_id,
        start_date=start_date,
        end_date=end_date,
        version=version,
    )
    if run is None:
        emit(LOG, logging.WARNING, "query.current_run_missing",
             f"No current trend run for forum_id={forum_id} window=[{start_date}, {end_date}]",
             result="failure", status="not_found",
             forum_id=forum_id, error_code="DISCUSSION_TREND_NO_CURRENT_RUN")
        return None, {
            "code": "DISCUSSION_TREND_NO_CURRENT_RUN",
            "message": (
                f"No current trend run for forum_id={forum_id} "
                f"window=[{start_date}, {end_date}] version={version}. "
                "Create a discussion trend index job first."
            ),
            "agent_hint": (
                "Use create_discussion_trend_index_job to build the trend mart "
                "for this window, then retry the query."
            ),
            "retryable": True,
        }
    if run.status != "succeeded":
        emit(LOG, logging.WARNING, "query.run_not_succeeded",
             f"Run {run.run_id} status={run.status} for forum_id={forum_id}",
             result="failure", status=run.status,
             forum_id=forum_id, run_id=run.run_id, error_code="DISCUSSION_TREND_RUN_NOT_SUCCEEDED")
        return None, {
            "code": "DISCUSSION_TREND_RUN_NOT_SUCCEEDED",
            "message": (
                f"Current run {run.run_id} has status={run.status}, "
                "not succeeded. Wait for the index job to complete."
            ),
            "agent_hint": (
                "Poll read_job with the trend index job_id until it reaches "
                "a terminal state, then retry the query."
            ),
            "retryable": True,
        }
    return run, None


def _common_envelope(run: Any, *, forum_id: int, start_date: str, end_date: str, version: str) -> dict[str, Any]:
    return {
        "forum_id": forum_id,
        "start_date": start_date,
        "end_date": end_date,
        "version": version,
        "run_id": run.run_id,
        "effective_data_until": run.completed_at,
        "coverage": {
            "status": run.status,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        },
        "warnings": list(run.warnings_json) if run.warnings_json else [],
    }


def get_discussion_partition_trends(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str = DEFAULT_VERSION,
    granularity: str = "day",
) -> AgentResult:
    if granularity not in VALID_GRANULARITIES:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message=f"granularity must be one of {sorted(VALID_GRANULARITIES)}, got {granularity!r}",
                agent_hint="Use 'day' or 'month'.",
            ),
        )

    conn, repo = _open_repo()
    try:
        run, err = _resolve_current_run(repo, forum_id, start_date, end_date, version)
        if err is not None:
            return AgentResult(ok=False, error=AgentError(**err))

        daily_rows = repo.get_partition_daily(run.run_id, forum_id)
        envelope = _common_envelope(run, forum_id=forum_id, start_date=start_date, end_date=end_date, version=version)

        if granularity == "month":
            monthly: dict[str, dict[str, Any]] = {}
            for row in daily_rows:
                month_key = row["bucket_date"][:7]
                if month_key not in monthly:
                    monthly[month_key] = {
                        "bucket_date": month_key,
                        "thread_count": 0,
                        "post_count": 0,
                        "active_user_count": 0,
                        "new_thread_count": 0,
                        "reply_count": 0,
                    }
                agg = monthly[month_key]
                agg["thread_count"] += row["thread_count"]
                agg["post_count"] += row["post_count"]
                agg["active_user_count"] += row["active_user_count"]
                agg["new_thread_count"] += row["new_thread_count"]
                agg["reply_count"] += row["reply_count"]
            envelope["granularity"] = "month"
            envelope["buckets"] = sorted(monthly.values(), key=lambda b: b["bucket_date"])
        else:
            envelope["granularity"] = "day"
            envelope["buckets"] = [
                {
                    "bucket_date": row["bucket_date"],
                    "thread_count": row["thread_count"],
                    "post_count": row["post_count"],
                    "active_user_count": row["active_user_count"],
                    "new_thread_count": row["new_thread_count"],
                    "reply_count": row["reply_count"],
                }
                for row in daily_rows
            ]

        return AgentResult(ok=True, data=envelope)
    finally:
        conn.close()


def get_discussion_topic_trends(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str = DEFAULT_VERSION,
    top_k: int = 20,
    min_floor_count: int | None = None,
    min_thread_count: int | None = None,
    min_user_count: int | None = None,
    min_confidence: float | None = None,
) -> AgentResult:
    conn, repo = _open_repo()
    try:
        run, err = _resolve_current_run(repo, forum_id, start_date, end_date, version)
        if err is not None:
            return AgentResult(ok=False, error=AgentError(**err))

        rows = repo.get_topic_daily_with_labels(run.run_id, forum_id)
        envelope = _common_envelope(run, forum_id=forum_id, start_date=start_date, end_date=end_date, version=version)

        ranked_topics = aggregate_topic_rows(rows, include_daily=True)

        # Apply caller-side filters
        filtered: list[dict[str, Any]] = []
        for t in ranked_topics:
            if min_thread_count is not None and t["total_thread_count"] < min_thread_count:
                continue
            if min_user_count is not None and t["total_user_count"] < min_user_count:
                continue
            if min_confidence is not None and (t["confidence"] is None or t["confidence"] < min_confidence):
                continue
            # min_floor_count: use total_post_count as proxy
            if min_floor_count is not None and t["total_post_count"] < min_floor_count:
                continue
            filtered.append(t)

        # Sort by total assignment_count descending, then take top_k
        filtered.sort(key=lambda t: t["total_assignment_count"], reverse=True)
        ranked = filtered[:top_k]

        if len(ranked) < MIN_TOPICS:
            envelope["topics"] = []
            envelope["warnings"] = envelope.get("warnings", []) + ["TOPIC_QUALITY_INSUFFICIENT"]
        else:
            envelope["topics"] = ranked

        envelope["top_k"] = top_k
        return AgentResult(ok=True, data=envelope, warnings=envelope.get("warnings", []))
    finally:
        conn.close()


def get_discussion_user_trends(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str = DEFAULT_VERSION,
    top_k: int = 20,
    sort_by: str = "post_count",
) -> AgentResult:
    if sort_by not in VALID_SORT_BY:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message=f"sort_by must be one of {sorted(VALID_SORT_BY)}, got {sort_by!r}",
                agent_hint="Use 'post_count', 'thread_count', or 'topic_count'.",
            ),
        )

    conn, repo = _open_repo()
    try:
        run, err = _resolve_current_run(repo, forum_id, start_date, end_date, version)
        if err is not None:
            return AgentResult(ok=False, error=AgentError(**err))

        rows = repo.get_user_daily(run.run_id, forum_id)
        envelope = _common_envelope(run, forum_id=forum_id, start_date=start_date, end_date=end_date, version=version)

        users = aggregate_user_rows(rows, include_daily=True)

        ranked = sorted(
            users,
            key=lambda u: u[f"total_{sort_by}"],
            reverse=True,
        )[:top_k]

        envelope["users"] = ranked
        envelope["top_k"] = top_k
        envelope["sort_by"] = sort_by
        return AgentResult(ok=True, data=envelope)
    finally:
        conn.close()


def get_discussion_report(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    version: str = DEFAULT_VERSION,
    format: str = "json",
    report_kind: str | None = None,
) -> AgentResult:
    if format not in ("json", "markdown"):
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message=f"format must be 'json' or 'markdown', got {format!r}",
            ),
        )

    conn, repo = _open_repo()
    try:
        run, err = _resolve_current_run(repo, forum_id, start_date, end_date, version)
        if err is not None:
            return AgentResult(ok=False, error=AgentError(**err))

        reports = repo.get_report_runs(run.run_id)
        if report_kind:
            reports = [r for r in reports if r["report_kind"] == report_kind]
        if not reports:
            return AgentResult(
                ok=False,
                error=AgentError(
                    code="DISCUSSION_REPORT_NOT_FOUND",
                    message=(
                        f"No report artifact found for run {run.run_id}. "
                        "Create a discussion trend report job first."
                    ),
                    agent_hint="Use create_discussion_trend_report_job to generate report artifacts for this window.",
                    retryable=True,
                ),
            )

        envelope = _common_envelope(run, forum_id=forum_id, start_date=start_date, end_date=end_date, version=version)
        envelope["reports"] = [
            {
                "report_kind": r["report_kind"],
                "report_json" if format == "json" else "report_markdown": (
                    r["report_json"] if format == "json" else r["report_markdown"]
                ),
                "created_at": r["created_at"],
            }
            for r in reports
        ]
        return AgentResult(ok=True, data=envelope)
    finally:
        conn.close()


def get_discussion_topic_evidence(
    *,
    forum_id: int,
    start_date: str,
    end_date: str,
    topic_id: int | None = None,
    topic_label: str | None = None,
    version: str = DEFAULT_VERSION,
    top_k: int = 10,
    mode: str = "auto",
) -> AgentResult:
    if mode not in VALID_EVIDENCE_MODES:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message=f"mode must be one of {sorted(VALID_EVIDENCE_MODES)}, got {mode!r}",
                agent_hint="Use 'auto', 'sql', or 'rag'.",
            ),
        )
    if topic_id is None and not topic_label:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message="Either topic_id or topic_label must be provided.",
                agent_hint="Provide a topic_id (int) or topic_label (str) to retrieve evidence.",
            ),
        )

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        backend = getattr(conn, "backend", None)
        if backend not in {"postgres", "postgresql"}:
            emit(LOG, logging.ERROR, "query.postgres_required",
                 f"Topic evidence requires PostgreSQL for forum_id={forum_id}",
                 result="failure", status="unsupported",
                 forum_id=forum_id, error_code="DISCUSSION_TREND_POSTGRES_REQUIRED")
            return AgentResult(
                ok=False,
                error=AgentError(
                    code="DISCUSSION_TREND_POSTGRES_REQUIRED",
                    message="Discussion topic evidence requires a PostgreSQL backend.",
                    agent_hint="Connect to a PostgreSQL database to use discussion trend features.",
                ),
            )

        repo = DiscussionTrendRepository(conn)
        run, err = _resolve_current_run(repo, forum_id, start_date, end_date, version)
        if err is not None:
            return AgentResult(ok=False, error=AgentError(**err))

        # Resolve topic
        resolved_topic: dict[str, Any] | None = None
        if topic_id is not None:
            for t in repo.get_topics_for_run(run.run_id):
                if t["topic_id"] == topic_id:
                    resolved_topic = t
                    break
        else:
            for t in repo.get_topics_for_run(run.run_id):
                if t["topic_label"] == topic_label:
                    resolved_topic = t
                    break

        if resolved_topic is None:
            lookup = f"topic_id={topic_id}" if topic_id is not None else f"topic_label={topic_label!r}"
            emit(LOG, logging.WARNING, "query.topic_not_found",
                 f"Topic {lookup} not found in run {run.run_id}",
                 result="failure", status="not_found",
                 forum_id=forum_id, run_id=run.run_id, error_code="DISCUSSION_TOPIC_NOT_FOUND")
            return AgentResult(
                ok=False,
                error=AgentError(
                    code="DISCUSSION_TOPIC_NOT_FOUND",
                    message=f"Topic {lookup} not found in current run {run.run_id}.",
                    agent_hint="Verify the topic exists in the current trend run. Use get_discussion_topic_trends to list available topics.",
                    retryable=False,
                ),
            )

        resolved_id: int = resolved_topic["topic_id"]
        topic_key: str = resolved_topic["topic_key"]
        topic_label_resolved: str = resolved_topic["topic_label"]
        warnings: list[str] = list(run.warnings_json) if run.warnings_json else []

        # RAG attempt
        rag_items: list[dict[str, Any]] | None = None
        rag_error: str | None = None
        if mode in ("auto", "rag"):
            rag_items, rag_error = _try_rag_topic_evidence(
                conn=conn,
                topic_label=topic_label_resolved,
                topic_key=topic_key,
                resolved_topic=resolved_topic,
                forum_id=forum_id,
                start_date=start_date,
                end_date=end_date,
                top_k=top_k,
                settings=settings,
            )

        if mode == "rag":
            if rag_items is None:
                return AgentResult(
                    ok=False,
                    error=AgentError(
                        code="DISCUSSION_RAG_EVIDENCE_UNAVAILABLE",
                        message=f"RAG evidence unavailable: {rag_error or 'no results'}",
                        agent_hint="The RAG index may not cover this topic's threads. Retry with mode='auto' for SQL fallback, or create RAG index jobs for the relevant threads.",
                        retryable=True,
                    ),
                )
            items = rag_items
            retrieval_mode = "rag"
        elif mode == "auto" and rag_items is not None:
            items = rag_items
            retrieval_mode = "rag"
        else:
            # SQL fallback
            if rag_error:
                warnings.append(RAG_SQL_FALLBACK_WARNING)
                emit(LOG, logging.INFO, "query.rag_fallback_used",
                     f"RAG unavailable for topic {topic_label_resolved}, falling back to SQL",
                     result="degraded", status="ok",
                     fallback_mode="sql", fallback_reason=rag_error,
                     forum_id=forum_id, run_id=run.run_id)
            items = repo.get_topic_floor_evidence(
                run_id=run.run_id,
                topic_id=resolved_id,
                forum_id=forum_id,
                top_k=top_k,
            )
            retrieval_mode = "sql"

        # Enforce per-tid diversity: max 3 per thread
        items = _enforce_thread_diversity(items, max_per_tid=3)

        envelope = _common_envelope(run, forum_id=forum_id, start_date=start_date, end_date=end_date, version=version)
        envelope.update({
            "topic_id": resolved_id,
            "topic_key": topic_key,
            "topic_label": topic_label_resolved,
            "retrieval_mode": retrieval_mode,
            "query_terms": [topic_label_resolved],
            "items": items,
            "warnings": warnings,
            "retrieval_diagnostics": {
                "rag_error": rag_error,
                "sql_fallback_used": retrieval_mode == "sql" and mode in ("auto", "rag"),
            },
        })
        return AgentResult(ok=True, data=envelope, warnings=warnings)
    finally:
        conn.close()


def _try_rag_topic_evidence(
    *,
    conn: Any,
    topic_label: str,
    topic_key: str,
    resolved_topic: dict[str, Any],
    forum_id: int,
    start_date: str,
    end_date: str,
    top_k: int,
    settings: Any,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Try RAG-based topic evidence. Returns (items, error_message)."""
    try:
        query_terms = [topic_label]
        if topic_key:
            # Extract readable part from topic key
            key_part = topic_key.split(":", 1)[-1] if ":" in topic_key else topic_key
            query_terms.append(key_part.replace("_", " "))
        rag_query = " ".join(query_terms)

        chunks_repo = RagChunksRepository(conn)
        keyword_rows = chunks_repo.keyword_search(
            query=rag_query,
            top_k=top_k * 2,
            forum_id=forum_id,
        )

        vector_rows: list[Any] = []
        try:
            provider = build_embedding_provider(settings)
            query_embedding = provider.embed_texts([rag_query])[0]
            vector_rows = get_vector_repository(conn).search(
                query_embedding=query_embedding,
                top_k=top_k * 2,
                forum_id=forum_id,
            )
        except (RagVectorUnavailableError, LLMRequestError, OSError, TimeoutError, IndexError):
            pass

        items = merge_rag_search_rows(
            keyword_rows=keyword_rows,
            vector_rows=vector_rows,
            top_k=top_k,
        )

        if not items:
            return None, "RAG returned no results for this topic"
        return items, None
    except Exception as exc:
        return None, str(exc)


def _enforce_thread_diversity(
    items: list[dict[str, Any]], max_per_tid: int = 3
) -> list[dict[str, Any]]:
    """Limit the number of evidence items per thread."""
    tid_counts: dict[int, int] = {}
    result: list[dict[str, Any]] = []
    for item in items:
        tid = item["tid"]
        count = tid_counts.get(tid, 0)
        if count >= max_per_tid:
            continue
        tid_counts[tid] = count + 1
        result.append(item)
    return result


def get_forum_evidence_pack(
    *,
    forum_id: int,
    query: str,
    start_date: str | None = None,
    end_date: str | None = None,
    top_k: int = 10,
    mode: str = "auto",
    intent: str = "general_research",
    require_current_run: bool = False,
) -> AgentResult:
    if mode not in VALID_EVIDENCE_MODES:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message=f"mode must be one of {sorted(VALID_EVIDENCE_MODES)}, got {mode!r}",
                agent_hint="Use 'auto', 'sql', or 'rag'.",
            ),
        )
    if intent not in VALID_EVIDENCE_INTENTS:
        return AgentResult(
            ok=False,
            error=AgentError(
                code="INVALID_ARGUMENT",
                message=f"intent must be one of {sorted(VALID_EVIDENCE_INTENTS)}, got {intent!r}",
                agent_hint="Use one of the supported research intents.",
            ),
        )

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        # Backend guard
        backend = getattr(conn, "backend", None)
        if backend not in {"postgres", "postgresql"}:
            emit(LOG, logging.ERROR, "query.postgres_required",
                 f"Forum evidence pack requires PostgreSQL for forum_id={forum_id}",
                 result="failure", status="unsupported",
                 forum_id=forum_id, error_code="DISCUSSION_TREND_POSTGRES_REQUIRED")
            return AgentResult(
                ok=False,
                error=AgentError(
                    code="DISCUSSION_TREND_POSTGRES_REQUIRED",
                    message="Forum evidence pack requires a PostgreSQL backend.",
                    agent_hint="Connect to a PostgreSQL database to use discussion trend features.",
                ),
            )

        # If require_current_run, validate it exists
        warnings: list[str] = []
        run_id: str | None = None
        if require_current_run and start_date and end_date:
            repo = DiscussionTrendRepository(conn)
            run, err = _resolve_current_run(
                repo, forum_id, start_date, end_date, DEFAULT_VERSION,
            )
            if err is not None:
                return AgentResult(ok=False, error=AgentError(**err))
            run_id = run.run_id
            warnings = list(run.warnings_json) if run.warnings_json else []

        # Try RAG/hybrid retrieval
        items, rag_error, retrieval_mode = _try_forum_evidence_hybrid(
            conn=conn,
            query=query,
            forum_id=forum_id,
            top_k=top_k,
            mode=mode,
            intent=intent,
            settings=settings,
        )

        if items is None:
            # RAG failed, fallback to SQL
            if mode == "rag":
                return AgentResult(
                    ok=False,
                    error=AgentError(
                        code="DISCUSSION_RAG_EVIDENCE_UNAVAILABLE",
                        message=f"RAG evidence unavailable: {rag_error or 'no results'}",
                        agent_hint="The RAG index may not cover this forum. Retry with mode='auto' for SQL fallback.",
                        retryable=True,
                    ),
                )
            # mode=auto: fallback to SQL
            repo = DiscussionTrendRepository(conn)
            sql_rows = repo.search_forum_floor_snippets(
                forum_id=forum_id,
                query=query,
                top_k=top_k,
                start_date=start_date,
                end_date=end_date,
            )
            items = sql_rows
            retrieval_mode = "sql"
            if rag_error:
                warnings.append(RAG_SQL_FALLBACK_WARNING)

        # Enforce thread diversity
        items = _enforce_thread_diversity(items, max_per_tid=3)

        # Intent-based reordering
        items = _apply_intent_order(items, intent)

        data: dict[str, Any] = {
            "forum_id": forum_id,
            "query": query,
            "intent": intent,
            "retrieval_mode": retrieval_mode,
            "query_terms": [t.strip() for t in query.replace("，", " ").replace("、", " ").split() if t.strip()],
            "items": items,
            "diversity": {
                "max_per_tid": 3,
                "total_tids": len({it["tid"] for it in items}),
            },
            "limitations": [
                "Evidence is retrieved via keyword/vector search and may miss semantically related but lexically different discussions.",
                "Thread diversity is enforced (max 3 items per thread), which may exclude relevant content from popular threads.",
            ],
            "warnings": warnings,
            "retrieval_diagnostics": {
                "rag_error": rag_error,
                "sql_fallback_used": retrieval_mode == "sql" and mode in ("auto", "rag"),
            },
        }
        if run_id:
            data["run_id"] = run_id

        return AgentResult(ok=True, data=data, warnings=warnings)
    finally:
        conn.close()


def _try_forum_evidence_hybrid(
    *,
    conn: Any,
    query: str,
    forum_id: int,
    top_k: int,
    mode: str,
    intent: str,
    settings: Any,
) -> tuple[list[dict[str, Any]] | None, str | None, str]:
    """Try hybrid (keyword + vector) retrieval for forum evidence.

    Returns (items, error_message, retrieval_mode).
    items=None means retrieval failed and caller should fall back.
    """
    if mode == "sql":
        return None, None, "sql"

    try:
        chunks_repo = RagChunksRepository(conn)
        keyword_rows = chunks_repo.keyword_search(
            query=query,
            top_k=top_k * 2,
            forum_id=forum_id,
        )

        vector_rows: list[Any] = []
        try:
            provider = build_embedding_provider(settings)
            query_embedding = provider.embed_texts([query])[0]
            vector_rows = get_vector_repository(conn).search(
                query_embedding=query_embedding,
                top_k=top_k * 2,
                forum_id=forum_id,
            )
        except (RagVectorUnavailableError, LLMRequestError, OSError, TimeoutError, IndexError):
            pass

        if not keyword_rows and not vector_rows:
            return None, "no results from keyword or vector search", "rag"

        items = merge_rag_search_rows(
            keyword_rows=keyword_rows,
            vector_rows=vector_rows,
            top_k=top_k,
        )

        return items, None, "rag"
    except Exception as exc:
        return None, str(exc), "rag"


def _apply_intent_order(
    items: list[dict[str, Any]], intent: str
) -> list[dict[str, Any]]:
    """Apply intent-specific reordering to evidence items. Does not change scores."""
    if not items or intent == "general_research":
        return items

    if intent == "slang_usage":
        # Prefer diverse threads and users for slang context
        seen_users: set[str] = set()
        ordered: list[dict[str, Any]] = []
        rest: list[dict[str, Any]] = []
        for it in items:
            pub = it.get("publisher")
            if pub and pub not in seen_users:
                seen_users.add(pub)
                ordered.append(it)
            else:
                rest.append(it)
        return ordered + rest

    if intent == "community_atmosphere":
        # Prefer items with title (thread context) and diverse threads
        with_title = [it for it in items if it.get("display_title")]
        without_title = [it for it in items if not it.get("display_title")]
        return with_title + without_title

    if intent in ("topic_investigation", "trend_context"):
        # Prioritize longer snippets (more context) for investigation
        return sorted(items, key=lambda it: len(it.get("snippet", "")), reverse=True)

    return items
