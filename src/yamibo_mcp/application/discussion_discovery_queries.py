from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

from sqlalchemy.exc import DBAPIError

from yamibo_mcp.application.contracts import AgentError, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.discussion_search import (
    DEFAULT_FLOOR_MATCHES,
    DEFAULT_TIMEOUT_MS,
    MAX_FLOOR_MATCHES,
    MAX_LIMIT,
    MAX_TIDS,
    MAX_TIMEOUT_MS,
    DiscussionSearchRepository,
    has_indexable_trigram,
    install_query_budget,
    parse_date_bound,
)


def find_discussions(
    *,
    query: str = "",
    forum_ids: Sequence[int] | None = None,
    tids: Sequence[int] | None = None,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    limit: int = 10,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    floor_match_limit: int = DEFAULT_FLOOR_MATCHES,
) -> AgentResult:
    """Find discussion candidates from raw thread titles and explicitly scoped floors.

    Short terms still search scoped thread titles, with a result cap and query
    timeout, but broad floor search remains INDEX_UNAVAILABLE because pg_trgm
    cannot extract a selective index key. PostgreSQL broad floor search uses the
    pg_trgm GIN index for terms of at least a three-character alphanumeric run;
    explicit-TID body search stays available. Date-only start_date is inclusive at midnight UTC; date-only end_date
    includes that full UTC calendar day and becomes the following day's exclusive bound.
    """
    if not isinstance(query, str):
        return _error("INVALID_ARGUMENT", "query must be a string.")
    normalized_query = query.strip()
    if len(normalized_query) > 200:
        return _error("INVALID_ARGUMENT", "query must be at most 200 characters.")
    if limit < 1 or limit > MAX_LIMIT:
        return _error("INVALID_ARGUMENT", f"limit must be between 1 and {MAX_LIMIT}.")
    if timeout_ms < 1 or timeout_ms > MAX_TIMEOUT_MS:
        return _error("INVALID_ARGUMENT", f"timeout_ms must be between 1 and {MAX_TIMEOUT_MS}.")
    if floor_match_limit < 1 or floor_match_limit > MAX_FLOOR_MATCHES:
        return _error(
            "INVALID_ARGUMENT",
            f"floor_match_limit must be between 1 and {MAX_FLOOR_MATCHES}.",
        )
    if not normalized_query and not tids:
        return _error("INVALID_ARGUMENT", "query or explicit tids is required.")

    try:
        normalized_tids = list(dict.fromkeys(int(tid) for tid in (tids or ())))
        requested_forums = list(dict.fromkeys(int(forum_id) for forum_id in (forum_ids or ())))
    except (TypeError, ValueError):
        return _error("INVALID_ARGUMENT", "tids and forum_ids must contain integers.")
    if any(tid <= 0 for tid in normalized_tids):
        return _error("INVALID_ARGUMENT", "tids must contain positive integers.")
    if len(normalized_tids) > MAX_TIDS:
        return _error("INVALID_ARGUMENT", f"at most {MAX_TIDS} tids may be searched at once.")
    if any(forum_id <= 0 for forum_id in requested_forums):
        return _error("INVALID_ARGUMENT", "forum_ids must contain positive integers.")

    try:
        start_at = parse_date_bound(start_date)
        end_at = parse_date_bound(end_date, end=True)
    except (TypeError, ValueError):
        return _error("INVALID_ARGUMENT", "Dates must be ISO date or datetime values.")
    if start_at is not None and end_at is not None and start_at >= end_at:
        return _error("INVALID_ARGUMENT", "start_date must be earlier than end_date.")

    conn = connect(load_settings().db_path)
    cleanup_budget = lambda: None
    try:
        repo = DiscussionSearchRepository(conn)
        allowed_forums = repo.discussion_forum_ids()
        if requested_forums:
            invalid = sorted(set(requested_forums) - set(allowed_forums))
            if invalid:
                return _error(
                    "INVALID_FORUM_SCOPE",
                    "Only enabled discussion forums can be searched.",
                )
            selected_forums = requested_forums
        else:
            selected_forums = allowed_forums

        cleanup_budget = install_query_budget(conn, timeout_ms)
        result = repo.search(
            query=normalized_query,
            forum_ids=selected_forums,
            tids=normalized_tids,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            floor_match_limit=floor_match_limit,
        )
        body_status = result["body_search_status"]
        data = {
            "query": normalized_query,
            "actual_mode": "title_and_floor_text" if body_status == "searched" else "title_only",
            "body_search_status": body_status,
            "result_status": "partial_index_unavailable" if body_status == "INDEX_UNAVAILABLE" else "complete",
            "forum_ids": selected_forums,
            "tids": normalized_tids,
            "start_date": start_at.isoformat() if start_at else None,
            "end_date_exclusive": end_at.isoformat() if end_at else None,
            "date_semantics": "start_date is inclusive; a date-only end_date includes that UTC day, reported as end_date_exclusive.",
            "limit": limit,
            "count": len(result["items"]),
            "items": result["items"],
            "budget": {
                "timeout_ms": timeout_ms,
                "floor_match_limit": floor_match_limit,
                "floor_rows_returned": result["floor_rows_returned"],
                "floor_match_limit_reached": result["floor_match_limit_reached"],
            },
        }
        warnings = []
        if body_status == "INDEX_UNAVAILABLE":
            if normalized_query and not normalized_tids and not has_indexable_trigram(normalized_query):
                warnings.append(
                    "INDEX_UNAVAILABLE: matching thread titles were searched, but broad floor-text search was skipped because this short term has no selective trigram index key. Results are partial."
                )
            else:
                warnings.append(
                    "INDEX_UNAVAILABLE: broad floor-text search requires the valid PostgreSQL trigram index."
                )
        if result["floor_match_limit_reached"]:
            warnings.append("FLOOR_MATCH_LIMIT_REACHED: floor matches may be truncated at the configured limit.")
        return AgentResult(ok=True, data=data, warnings=warnings)
    except (TimeoutError, DBAPIError) as exc:
        if _is_timeout(exc):
            return _error("QUERY_TIMEOUT", "Discussion search exceeded its query time budget.", retryable=True)
        return _error("QUERY_FAILED", "Discussion search failed while reading the database.", retryable=True)
    except Exception:
        return _error("QUERY_FAILED", "Discussion search failed while reading the database.", retryable=True)
    finally:
        try:
            cleanup_budget()
        finally:
            conn.close()


def _is_timeout(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, TimeoutError):
            return True
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        message = str(current).lower()
        if sqlstate == "57014" or "interrupted" in message or "statement timeout" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _error(code: str, message: str, *, retryable: bool = False) -> AgentResult:
    return AgentResult(
        ok=False,
        error=AgentError(
            code=code,
            message=message,
            agent_hint=message,
            retryable=retryable,
        ),
    )
