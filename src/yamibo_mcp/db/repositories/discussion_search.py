from __future__ import annotations

import time
from datetime import date, datetime, time as datetime_time, timezone
from typing import Any


MAX_LIMIT = 50
MAX_TIDS = 25
MAX_TIMEOUT_MS = 5_000
DEFAULT_TIMEOUT_MS = 1_500
MAX_FLOOR_MATCHES = 20_000
DEFAULT_FLOOR_MATCHES = 5_000
SNIPPET_MAX_LEN = 220
FLOOR_CONTENT_TRGM_INDEX = "idx_floors_content_trgm"


class DiscussionSearchRepository:
    """Search discussion titles and use an indexed path for broad floor search."""

    def __init__(self, conn: Any):
        self.conn = conn
        self.backend = getattr(conn, "backend", "sqlite")

    def discussion_forum_ids(self) -> list[int]:
        enabled = "TRUE" if self._is_postgres() else "1"
        rows = self.conn.execute(
            f"SELECT forum_id FROM forums WHERE content_kind = ? AND enabled = {enabled} ORDER BY forum_id",
            ("discussion",),
        ).fetchall()
        return [int(row["forum_id"]) for row in rows]

    def search(
        self,
        *,
        query: str,
        forum_ids: list[int],
        tids: list[int],
        start_at: datetime | str | None,
        end_at: datetime | str | None,
        limit: int,
        floor_match_limit: int,
    ) -> dict[str, Any]:
        if not forum_ids:
            return {
                "items": [],
                "floor_rows_returned": 0,
                "floor_match_limit_reached": False,
                "thread_rows_scanned": 0,
                "body_search_status": "searched" if query else "not_requested",
            }
        body_search_status = self._body_search_status(query=query, tids=tids)
        # Title fallback is safe for short terms: it searches only the bounded
        # threads table scope and returns at most `limit` rows. The application
        # installs a per-query statement timeout. Broad floor scans remain gated
        # separately by a usable trigram path (or explicit TIDs).
        title_rows, thread_rows_scanned = self._search_titles(
            query=query,
            forum_ids=forum_ids,
            tids=tids,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
        floor_rows = self._search_floors(
            query=query,
            forum_ids=forum_ids,
            tids=tids,
            start_at=start_at,
            end_at=end_at,
            floor_match_limit=floor_match_limit,
            body_search_status=body_search_status,
        )
        floor_match_limit_reached = len(floor_rows) > floor_match_limit
        floor_rows = floor_rows[:floor_match_limit]

        merged: dict[int, dict[str, Any]] = {}
        floor_hit_tids: set[int] = set()
        for row in title_rows:
            tid = int(row["tid"])
            merged[tid] = self._item(row, query=query, source="title")
        for row in floor_rows:
            tid = int(row["tid"])
            if tid in floor_hit_tids:
                continue
            floor_hit_tids.add(tid)
            item = merged.get(tid)
            if item is None:
                item = self._item(row, query=query, source="floor")
                merged[tid] = item
            else:
                item["match_type"] = "title_and_floor"
                item["pid"] = int(row["pid"])
                item["snippet"] = _build_snippet(str(row["content"] or ""), query)
                item["matched_at"] = "floor"

        items = sorted(
            merged.values(),
            key=lambda item: (str(item.get("pub_time") or ""), int(item["tid"])),
            reverse=True,
        )[:limit]
        return {
            "items": items,
            "floor_rows_returned": len(floor_rows),
            "floor_match_limit_reached": floor_match_limit_reached,
            "thread_rows_scanned": thread_rows_scanned,
            "body_search_status": body_search_status,
        }

    def _body_search_status(self, *, query: str, tids: list[int]) -> str:
        if not query:
            return "not_requested"
        if tids:
            return "searched"
        if not self._is_postgres() or not has_indexable_trigram(query):
            return "INDEX_UNAVAILABLE"
        return "searched" if self._floor_trigram_index_ready() else "INDEX_UNAVAILABLE"

    def _floor_trigram_index_ready(self) -> bool:
        """Fail closed unless the migration's GIN index is valid in this schema."""
        row = self.conn.execute(
            """
            SELECT i.indisvalid
            FROM pg_class AS index_class
            JOIN pg_namespace AS namespace ON namespace.oid = index_class.relnamespace
            JOIN pg_index AS i ON i.indexrelid = index_class.oid
            WHERE namespace.nspname = current_schema()
              AND index_class.relname = ?
            """,
            (FLOOR_CONTENT_TRGM_INDEX,),
        ).fetchone()
        return bool(row and row["indisvalid"])

    def _search_titles(self, *, query, forum_ids, tids, start_at, end_at, limit):
        where, params = self._thread_scope(forum_ids, tids, start_at, end_at)
        text_match = ""
        if query:
            like = f"%{_escape_like(query)}%"
            operator = "ILIKE" if self._is_postgres() else "LIKE"
            text_match = (
                f" AND (t.raw_title {operator} ? ESCAPE '\\'"
                f" OR t.display_title {operator} ? ESCAPE '\\')"
            )
            params.extend((like, like))
        # Scope predicates are applied before sorting and LIMIT. PostgreSQL may
        # use title trigram indexes for longer terms; short terms can scan the
        # scoped thread rows, but the per-search statement timeout bounds the work.
        sql = f"""
            SELECT t.tid, t.raw_title, t.display_title, t.pub_time, t.forum_id,
                   (SELECT f.pid FROM floors f WHERE f.tid = t.tid ORDER BY f.floor_no, f.pid LIMIT 1) AS pid
            FROM threads t
            {where}{text_match}
            ORDER BY t.pub_time DESC, t.tid DESC
            LIMIT ?
        """
        rows = self.conn.execute(sql, (*params, limit)).fetchall()
        return rows, len(rows)

    def _search_floors(
        self, *, query, forum_ids, tids, start_at, end_at,
        floor_match_limit, body_search_status,
    ):
        # Explicit TIDs use the existing tid index. Broad search is enabled only
        # when a term contains a >=3-character alphanumeric run and the PostgreSQL
        # trigram GIN index is valid; shorter runs have no selective trigram key.
        if not query or body_search_status != "searched":
            return []
        where, params = self._thread_scope(forum_ids, tids, None, None)
        floor_date = ""
        if start_at is not None:
            floor_date += " AND f.pub_time >= ?"
            params.append(self._date_parameter(start_at))
        if end_at is not None:
            floor_date += " AND f.pub_time < ?"
            params.append(self._date_parameter(end_at))
        operator = "ILIKE" if self._is_postgres() else "LIKE"
        sql = f"""
            SELECT f.pid, f.tid, f.floor_no, f.content, f.pub_time,
                   t.raw_title, t.display_title, t.pub_time AS thread_pub_time, t.forum_id
            FROM threads t JOIN floors f ON f.tid = t.tid
            {where}{floor_date}
              AND f.content {operator} ? ESCAPE '\\'
            ORDER BY f.pub_time DESC, f.pid DESC
            LIMIT ?
        """
        params.extend((f"%{_escape_like(query)}%", floor_match_limit + 1))
        return self.conn.execute(sql, params).fetchall()

    def _thread_scope(self, forum_ids, tids, start_at, end_at):
        clauses = [f"t.forum_id IN ({', '.join('?' for _ in forum_ids)})", "t.content_kind = ?"]
        params: list[Any] = [*forum_ids, "discussion"]
        if tids:
            clauses.append(f"t.tid IN ({', '.join('?' for _ in tids)})")
            params.extend(tids)
        if start_at is not None:
            clauses.append("t.pub_time >= ?")
            params.append(self._date_parameter(start_at))
        if end_at is not None:
            clauses.append("t.pub_time < ?")
            params.append(self._date_parameter(end_at))
        return "WHERE " + " AND ".join(clauses), params

    def _date_parameter(self, value):
        if self._is_postgres() or isinstance(value, datetime):
            return value
        return value.isoformat()

    def _is_postgres(self) -> bool:
        return self.backend in {"postgres", "postgresql"}

    def _item(self, row, *, query, source):
        title = str(row["display_title"] or row["raw_title"] or "")
        if source == "title":
            snippet = _build_snippet(title, query)
            pid = row["pid"]
            pub_time = row["pub_time"]
            match_type = "title"
        else:
            snippet = _build_snippet(str(row["content"] or ""), query)
            pid = row["pid"]
            pub_time = row["pub_time"] or row["thread_pub_time"]
            match_type = "floor"
        return {
            "tid": int(row["tid"]),
            "pid": int(pid) if pid is not None else None,
            "title": title,
            "pub_time": pub_time.isoformat() if hasattr(pub_time, "isoformat") else pub_time,
            "forum_id": int(row["forum_id"]),
            "snippet": snippet,
            "match_type": match_type,
            "matched_at": source,
        }


def install_query_budget(conn: Any, timeout_ms: int) -> callable:
    """Apply a per-search timeout and return a cleanup callback."""
    backend = getattr(conn, "backend", "sqlite")
    if backend in {"postgres", "postgresql"}:
        conn.execute("SELECT set_config('statement_timeout', ?, true)", (f"{timeout_ms}ms",))
        return lambda: None
    raw = getattr(conn, "raw_connection", None)
    driver = getattr(getattr(raw, "connection", None), "driver_connection", None)
    if driver is None:
        return lambda: None
    deadline = time.monotonic() + timeout_ms / 1000
    driver.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1_000)
    return lambda: driver.set_progress_handler(None, 0)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def has_indexable_trigram(value: str) -> bool:
    """Whether a substring pattern contains a word run pg_trgm can index."""
    run = 0
    for character in value:
        run = run + 1 if character.isalnum() else 0
        if run >= 3:
            return True
    return False


def _build_snippet(text: str, query: str, *, max_len: int = SNIPPET_MAX_LEN) -> str:
    if len(text) <= max_len:
        return text
    index = text.casefold().find(query.casefold()) if query else -1
    start = max(0, index - max_len // 3) if index >= 0 else 0
    end = min(len(text), start + max_len)
    if end - start < max_len:
        start = max(0, end - max_len)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def parse_date_bound(value: str | date | datetime | None, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
        date_only = False
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime_time.min, tzinfo=timezone.utc)
        date_only = True
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("date bound must not be empty")
        date_only = len(raw) == 10
        try:
            parsed = datetime.combine(date.fromisoformat(raw), datetime_time.min, tzinfo=timezone.utc)
        except ValueError:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        raise TypeError("date bound must be a string, date, datetime, or None")
    if end and date_only:
        from datetime import timedelta

        parsed += timedelta(days=1)
    return parsed.astimezone(timezone.utc)
