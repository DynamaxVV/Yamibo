from __future__ import annotations

import hashlib
import json
import threading

from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.web_fastapi.helpers import TTLCache


_CACHE_TTL_SECONDS = 30.0
_archive_counts_cache = TTLCache(ttl_seconds=_CACHE_TTL_SECONDS)
_thread_counts_cache = TTLCache(ttl_seconds=_CACHE_TTL_SECONDS)
_archive_counts_lock = threading.Lock()
_thread_counts_lock = threading.Lock()


def _scope_key(database_scope: str) -> str:
    return hashlib.sha256(database_scope.encode("utf-8")).hexdigest()[:16]


def get_archive_counts(repo: ThreadsRepository, *, database_scope: str) -> dict[str, object]:
    key = _scope_key(database_scope)
    cached = _archive_counts_cache.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    with _archive_counts_lock:
        cached = _archive_counts_cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        counts = {
            "thread_count": repo.count_threads(),
            "export_count": repo.count_exported_threads(),
            "forum_counts": {
                int(row["forum_id"]): int(row["cnt"])
                for row in repo.count_threads_by_forum()
            },
        }
        _archive_counts_cache.set(key, counts)
        return counts


def get_thread_list_count(
    repo: ThreadsRepository,
    *,
    q: str | None,
    forum_id: int | None,
    days: int | None,
    archive_status: str | None,
    database_scope: str,
) -> int | None:
    # Search counts have a high-cardinality key and should remain exact.
    if q and q.strip():
        return None
    key = f"{_scope_key(database_scope)}:{json.dumps([forum_id, days, archive_status], separators=(',', ':'))}"
    cached = _thread_counts_cache.get(key)
    if cached is not None:
        return int(cached)

    with _thread_counts_lock:
        cached = _thread_counts_cache.get(key)
        if cached is not None:
            return int(cached)
        count = repo.count_threads_filtered(
            forum_id=forum_id,
            days=days,
            archive_status=archive_status,
        )
        _thread_counts_cache.set(key, count)
        return count


def clear_archive_counts_cache() -> None:
    """Clear cached counts for tests and explicit future invalidation hooks."""
    _archive_counts_cache.clear()
    _thread_counts_cache.clear()
