"""Unit tests for discussion_trend_builder (V1, PostgreSQL-only)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from yamibo_mcp.application.discussion_trend_builder import (
    DEFAULT_TOPIC_THRESHOLDS,
    BuildInput,
    build_trend_mart,
)
from yamibo_mcp.db.connection import ResultProxy


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = list(rows or [])

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Fake DatabaseConnection that records calls and returns canned data.

    Supports:
    - execute() for SELECT window rows, UPDATE/INSERT run lifecycle, DELETE
    - executemany() for bulk topic/assignment/daily inserts (auto-assigns topic_id)
    - SELECT topic_id, topic_key ... → returns what was inserted
    """

    def __init__(self, *, backend: str = "postgres", window_rows: list[dict[str, Any]] | None = None) -> None:
        self.backend = backend
        self._window_rows = list(window_rows or [])
        self.calls: list[tuple[str, str, Any]] = []
        self.executemany_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self._inserts_by_table: dict[str, list[dict[str, Any]]] = {}
        self._next_topic_id = 0
        self.commit_calls = 0

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append(("execute", sql, parameters))
        if "FROM threads t" in sql:
            return _FakeResult(self._window_rows)
        if "SELECT topic_id, topic_key FROM discussion_topics" in sql:
            return _FakeResult(self._inserts_by_table.get("discussion_topics", []))
        if sql.lstrip().startswith("SELECT pg_advisory_xact_lock"):
            return _FakeResult()
        if sql.lstrip().startswith("INSERT INTO discussion_index_runs"):
            return _FakeResult()
        if sql.lstrip().startswith("UPDATE discussion_index_runs"):
            return _FakeResult()
        if sql.lstrip().startswith("INSERT INTO discussion_current_indexes"):
            return _FakeResult()
        if sql.lstrip().startswith("DELETE FROM"):
            return _FakeResult()
        return _FakeResult()

    def executemany(self, statement, seq_of_parameters):
        sql = str(statement)
        params_list = list(seq_of_parameters)
        self.executemany_calls.append((sql, params_list))
        for table in (
            "discussion_topics",
            "discussion_topic_assignments",
            "discussion_partition_daily",
            "discussion_topic_daily",
            "discussion_user_daily",
        ):
            if f"INSERT INTO {table}" in sql:
                rows = self._inserts_by_table.setdefault(table, [])
                for params in params_list:
                    if table == "discussion_topics":
                        self._next_topic_id += 1
                        rows.append({"topic_id": self._next_topic_id, "topic_key": params["topic_key"]})
                    else:
                        rows.append(dict(params))
                return _FakeResult()
        return _FakeResult()

    def commit(self) -> None:
        self.commit_calls += 1
        self.calls.append(("commit", "", None))

    # helpers -----------------------------------------------------------
    def insert_rows(self, table: str) -> list[dict[str, Any]]:
        return list(self._inserts_by_table.get(table, []))

    def find_executemany(self, table: str) -> tuple[str, list[dict[str, Any]]] | None:
        for sql, params in self.executemany_calls:
            if f"INSERT INTO {table}" in sql:
                return sql, params
        return None


def _row(
    tid: int,
    pid: int | None,
    floor_no: int | None,
    pub: datetime,
    *,
    publisher: str = "alice",
    publisher_uid: str = "uid1",
    category: str | None = "anime",
    core_title_guess: str | None = "测试轻小说",
    normalized_core_title: str | None = "测试轻小说",
    quote_text: str | None = None,
) -> dict[str, Any]:
    return {
        "tid": tid,
        "forum_id": 5,
        "category": category,
        "display_title": core_title_guess or "title",
        "raw_title": core_title_guess or "title",
        "thread_publisher": publisher,
        "thread_publisher_uid": publisher_uid,
        "thread_pub_time": pub,
        "pid": pid,
        "floor_no": floor_no,
        "floor_publisher": publisher,
        "floor_publisher_uid": publisher_uid,
        "floor_pub_time": pub,
        "quote_text": quote_text,
        "reply_text": None,
        "core_title_guess": core_title_guess,
        "normalized_core_title": normalized_core_title,
    }


def _build_window(
    base: datetime, *, threads: int, floors_per_thread: int, distinct_users: int
) -> list[dict[str, Any]]:
    """Build a synthetic window of (threads x floors_per_thread) rows with N distinct users."""
    rows: list[dict[str, Any]] = []
    for t in range(threads):
        tid = 1000 + t
        for f in range(floors_per_thread):
            pid = (f + 1)
            user_index = (t * floors_per_thread + f) % distinct_users
            rows.append(
                _row(
                    tid,
                    pid,
                    pid,
                    base.replace(hour=10 + (f % 8)),
                    publisher=f"user{user_index}",
                    publisher_uid=f"uid{user_index}",
                )
            )
    return rows


def test_builder_rejects_non_postgres_backend():
    conn = _FakeConn(backend="sqlite")
    payload = BuildInput(forum_id=5, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    with pytest.raises(ValueError, match="discussion trends require postgres backend"):
        build_trend_mart(conn, run_id="r-fail", payload=payload)


def test_builder_rejects_invalid_forum_id():
    conn = _FakeConn()
    payload = BuildInput(forum_id=0, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    with pytest.raises(ValueError, match="forum_id must be a positive integer"):
        build_trend_mart(conn, run_id="r-bad", payload=payload)


def test_builder_rejects_inverted_window():
    conn = _FakeConn()
    payload = BuildInput(forum_id=5, start_date=date(2024, 2, 1), end_date=date(2024, 1, 1))
    with pytest.raises(ValueError, match="start_date must be <= end_date"):
        build_trend_mart(conn, run_id="r-bad", payload=payload)


def test_builder_topic_quality_insufficient_emits_warning_and_no_ranking():
    """1 thread × 1 floor × 1 user → no topic qualifies → TOPIC_QUALITY_INSUFFICIENT."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = [_row(1000, 1, 1, base, category="anime")]
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(forum_id=5, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    result = build_trend_mart(conn, run_id="r-insuf", payload=payload)
    assert result.status == "succeeded"
    assert "TOPIC_QUALITY_INSUFFICIENT" in result.warnings
    assert result.metrics["topic_qualified_count"] == 0
    assert result.metrics["assignment_count"] == 0
    # partition_daily and user_daily must still be written
    assert conn.find_executemany("discussion_partition_daily") is not None
    assert conn.find_executemany("discussion_user_daily") is not None
    # but no topics, assignments, or topic_daily
    assert conn.find_executemany("discussion_topics") is None
    assert conn.find_executemany("discussion_topic_assignments") is None
    assert conn.find_executemany("discussion_topic_daily") is None


def test_builder_qualified_topic_emits_assignment_and_topic_daily():
    """3 threads × 7 floors × 5 users, manual_seed matches all → topic qualifies."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = _build_window(base, threads=3, floors_per_thread=7, distinct_users=5)
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(
        forum_id=5,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        manual_seed_topics=(("anime", "动漫综合"),),
    )
    result = build_trend_mart(conn, run_id="r-good", payload=payload)
    assert result.status == "succeeded"
    assert "TOPIC_QUALITY_INSUFFICIENT" not in result.warnings
    assert result.metrics["topic_qualified_count"] >= 1
    # category:anime and phrase:测试轻小说 both also qualify from the same data,
    # so assignment_count is topic_qualified_count * 3 threads
    assert result.metrics["assignment_count"] == result.metrics["topic_qualified_count"] * 3
    # all five mart tables written
    assert conn.find_executemany("discussion_topics") is not None
    assert conn.find_executemany("discussion_topic_assignments") is not None
    assert conn.find_executemany("discussion_topic_daily") is not None
    assert conn.find_executemany("discussion_partition_daily") is not None
    assert conn.find_executemany("discussion_user_daily") is not None
    # topic row uses the seed key/label
    topics_sql, topics_params = conn.find_executemany("discussion_topics")
    seed_topics = [p for p in topics_params if p["topic_source"] == "manual_seed"]
    assert len(seed_topics) == 1
    assert seed_topics[0]["topic_key"] == "seed:anime"
    assert seed_topics[0]["topic_label"] == "动漫综合"
    assert seed_topics[0]["topic_source"] == "manual_seed"
    assert seed_topics[0]["confidence"] == 0.9
    # assignments bound to the same run
    assigns_sql, assigns_params = conn.find_executemany("discussion_topic_assignments")
    assert assigns_params[0]["run_id"] == "r-good"
    # at least one assignment comes from the manual_seed topic
    assert any(a["assignment_source"] == "manual_seed" for a in assigns_params)
    # topic_id map is consistent: each topic_id maps to a unique topic_key
    topic_ids_used = {a["topic_id"] for a in assigns_params}
    assert len(topic_ids_used) == result.metrics["topic_qualified_count"]
    _user_sql, user_params = conn.find_executemany("discussion_user_daily")
    assert any(row["topic_count"] > 0 for row in user_params)


def test_builder_assignments_exclude_unqualified_topics():
    """A topic that fails thresholds must not produce assignments."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = _build_window(base, threads=3, floors_per_thread=7, distinct_users=5)
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(
        forum_id=5,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        manual_seed_topics=(
            ("anime", "动漫综合"),  # qualifies
            ("海", "海绵宝宝"),  # does not match any rows → fails thresholds
        ),
    )
    result = build_trend_mart(conn, run_id="r-mixed", payload=payload)
    # the "海" seed has zero support and is dropped
    assert result.metrics["topic_dropped_count"] >= 1
    assert result.metrics["topic_qualified_count"] >= 1
    assigns_sql, assigns_params = conn.find_executemany("discussion_topic_assignments")
    # No assignment should belong to the dropped "seed:海" topic
    for a in assigns_params:
        # assignments reference topic_id, not topic_key directly; ensure topic_id 2 (the dropped)
        # does not appear. Dropped seeds don't get a topic_id assigned (replace_topics skips them).
        pass
    # The dropped seed never produced a topic row, so its topic_id was never assigned.
    # Verify by inspecting topics inserted
    topics_sql, topics_params = conn.find_executemany("discussion_topics")
    inserted_keys = {p["topic_key"] for p in topics_params}
    assert "seed:海" not in inserted_keys
    assert "seed:anime" in inserted_keys


def test_builder_partition_and_user_daily_always_written_when_topic_empty():
    """Even with no qualified topics, partition_daily and user_daily must be present."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = _build_window(base, threads=2, floors_per_thread=2, distinct_users=2)
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(forum_id=5, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    result = build_trend_mart(conn, run_id="r-empty", payload=payload)
    # topic_qualified may be 0 since 2 threads < min_active_thread_count(3)
    assert "TOPIC_QUALITY_INSUFFICIENT" in result.warnings
    # partition + user daily still written
    partition_sql, partition_params = conn.find_executemany("discussion_partition_daily")
    assert len(partition_params) >= 1
    user_sql, user_params = conn.find_executemany("discussion_user_daily")
    assert len(user_params) == 2
    # topic_daily NOT written when no qualified topics
    assert conn.find_executemany("discussion_topic_daily") is None


def test_builder_title_phrase_needs_multiple_threads():
    """A normalized core title appearing in only one thread must NOT be a topic."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = []
    for f in range(7):
        rows.append(
            _row(
                1000,
                f + 1,
                f + 1,
                base.replace(hour=10 + (f % 8)),
                category="anime",
                core_title_guess="测试轻小说",
                normalized_core_title="测试轻小说",
            )
        )
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(forum_id=5, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    result = build_trend_mart(conn, run_id="r-phrase1", payload=payload)
    # single thread with title_phrase → does not qualify (need >= 2 distinct threads)
    assert result.metrics["topic_qualified_count"] == 0
    assert "TOPIC_QUALITY_INSUFFICIENT" in result.warnings


def test_builder_title_phrase_qualifies_across_two_threads():
    """Same core title in two distinct threads (with enough support) → qualifies.

    Note: default thresholds require min_floor_count=20 and min_active_thread_count=3.
    We lower both for this test to specifically exercise the title_phrase rules.
    """
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = []
    for tid in (1000, 1001):
        for f in range(7):
            rows.append(
                _row(
                    tid,
                    f + 1,
                    f + 1,
                    base.replace(hour=10 + (f % 8)),
                    category="anime",
                    core_title_guess="测试轻小说",
                    normalized_core_title="测试轻小说",
                    publisher=f"user{tid}-{f}",
                    publisher_uid=f"uid{tid}-{f}",
                )
            )
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(
        forum_id=5,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        thresholds={"min_floor_count": 5, "min_active_thread_count": 2, "min_topics": 1},
    )
    result = build_trend_mart(conn, run_id="r-phrase2", payload=payload)
    # 2 threads, 14 floors, 14 distinct users → title_phrase should qualify
    assert result.metrics["topic_qualified_count"] >= 1
    topics_sql, topics_params = conn.find_executemany("discussion_topics")
    sources = {p["topic_source"] for p in topics_params}
    assert "title_phrase" in sources
    assert "TOPIC_QUALITY_INSUFFICIENT" not in result.warnings


def test_builder_does_not_use_floor_keyword_for_topic_discovery():
    """No title_parse rows → no title_phrase topic, even though floors exist."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = []
    for tid in (1000, 1001):
        for f in range(7):
            rows.append(
                _row(
                    tid,
                    f + 1,
                    f + 1,
                    base.replace(hour=10 + (f % 8)),
                    category="anime",
                    core_title_guess=None,  # no parse → no title_phrase
                    normalized_core_title=None,
                    publisher=f"user{tid}-{f}",
                    publisher_uid=f"uid{tid}-{f}",
                )
            )
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(forum_id=5, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    result = build_trend_mart(conn, run_id="r-floor", payload=payload)
    executemany_table_names = {sql.split("INTO ", 1)[1].split(" ")[0] for sql, _ in conn.executemany_calls}
    # title_phrase must not appear; only category might (or none, if thresholds fail)
    assert "discussion_topics" not in executemany_table_names or all(
        p["topic_source"] != "title_phrase"
        for sql, params in conn.executemany_calls
        if "INSERT INTO discussion_topics" in sql
        for p in params
    )


def test_builder_pubtime_fallback_warning_emitted():
    """Rows with no floor_pub_time but with thread_pub_time → PUBTIME_FALLBACK warning."""
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = []
    for tid in (1000, 1001):
        for f in range(7):
            r = _row(
                tid,
                f + 1,
                f + 1,
                base.replace(hour=10 + (f % 8)),
                category="anime",
                publisher=f"user{tid}-{f}",
                publisher_uid=f"uid{tid}-{f}",
            )
            r["floor_pub_time"] = None  # force fallback
            rows.append(r)
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(forum_id=5, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
    result = build_trend_mart(conn, run_id="r-fallback", payload=payload)
    assert "PUBTIME_FALLBACK" in result.warnings


def test_builder_writes_run_lifecycle_and_current_index():
    base = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    rows = _build_window(base, threads=3, floors_per_thread=7, distinct_users=5)
    conn = _FakeConn(window_rows=rows)
    payload = BuildInput(
        forum_id=5,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        manual_seed_topics=(("anime", "动漫综合"),),
    )
    build_trend_mart(conn, run_id="r-lifecycle", payload=payload)
    sqls = [c[1] for c in conn.calls]
    assert any("INSERT INTO discussion_index_runs" in s for s in sqls)
    assert any("UPDATE discussion_index_runs" in s and "succeeded" in s for s in sqls)
    assert any("INSERT INTO discussion_current_indexes" in s for s in sqls)
    assert any("pg_advisory_xact_lock" in s for s in sqls)


def test_default_thresholds_match_spec():
    assert DEFAULT_TOPIC_THRESHOLDS["min_floor_count"] == 20
    assert DEFAULT_TOPIC_THRESHOLDS["min_active_thread_count"] == 3
    assert DEFAULT_TOPIC_THRESHOLDS["min_distinct_user_count"] == 5
    assert DEFAULT_TOPIC_THRESHOLDS["min_confidence_avg"] == 0.55
    assert DEFAULT_TOPIC_THRESHOLDS["min_topics"] == 3
