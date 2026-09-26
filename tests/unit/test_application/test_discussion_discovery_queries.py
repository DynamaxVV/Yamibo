from __future__ import annotations

from unittest.mock import MagicMock, patch

from yamibo_mcp.application.discussion_discovery_queries import find_discussions
from yamibo_mcp.db.repositories.discussion_search import DiscussionSearchRepository


def _thread(db, *, tid, title, forum_id=5, content_kind="discussion", pub_time="2020-01-01T00:00:00+00:00"):
    db.execute(
        """
        INSERT INTO threads (tid, page_type, raw_title, display_title, pub_time,
                             archive_status, validation_status, forum_id, content_kind)
        VALUES (?, 'discussion', ?, ?, ?, 'complete', 'valid', ?, ?)
        """,
        (tid, title, title, pub_time, forum_id, content_kind),
    )


def _floor(db, *, pid, tid, floor_no, content, pub_time):
    db.execute(
        "INSERT INTO floors (pid, tid, floor_no, content, pub_time, has_images) VALUES (?, ?, ?, ?, ?, 0)",
        (pid, tid, floor_no, content, pub_time),
    )


def _call_with_db(db, **kwargs):
    settings = MagicMock()
    settings.db_path = ":isolated-test-db:"
    with patch("yamibo_mcp.application.discussion_discovery_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.discussion_discovery_queries.connect", return_value=db):
        return find_discussions(**kwargs)


def test_title_search_filters_forum_and_content_kind_before_limit(db):
    _thread(db, tid=71001, title="目标讨论 标题", forum_id=5)
    _floor(db, pid=81001, tid=71001, floor_no=1, content="正文", pub_time="2020-01-01")
    _thread(db, tid=71002, title="目标讨论 错误类型", forum_id=5, content_kind="comic")
    _floor(db, pid=81002, tid=71002, floor_no=1, content="正文", pub_time="2020-01-01")
    _thread(db, tid=71003, title="目标讨论 错误板块", forum_id=30, content_kind="comic")
    _floor(db, pid=81003, tid=71003, floor_no=1, content="正文", pub_time="2020-01-01")

    result = _call_with_db(db, query="目标讨论", forum_ids=[5], limit=1)

    assert result.ok is True
    assert result.data["actual_mode"] == "title_only"
    assert result.data["body_search_status"] == "INDEX_UNAVAILABLE"
    assert result.data["result_status"] == "partial_index_unavailable"
    assert [item["tid"] for item in result.data["items"]] == [71001]
    assert result.warnings and result.warnings[0].startswith("INDEX_UNAVAILABLE:")


def test_explicit_tid_body_search_returns_actual_floor_pid_and_date_scope(db):
    _thread(db, tid=72001, title="一个旧讨论", forum_id=5, pub_time="2020-01-01T00:00:00+00:00")
    _floor(db, pid=82001, tid=72001, floor_no=1, content="旧楼层", pub_time="2026-09-20T00:00:00+00:00")
    _floor(db, pid=82002, tid=72001, floor_no=2, content="包含命中词的回复", pub_time="2026-09-25T00:00:00+00:00")

    result = _call_with_db(
        db,
        query="命中词",
        tids=[72001],
        start_date="2026-09-24",
        end_date="2026-09-25",
    )

    assert result.ok is True
    assert result.data["actual_mode"] == "title_and_floor_text"
    assert result.data["body_search_status"] == "searched"
    assert result.data["end_date_exclusive"] == "2026-09-26T00:00:00+00:00"
    assert "end_date includes that UTC day" in result.data["date_semantics"]
    assert result.data["items"] == [
        {
            "tid": 72001,
            "pid": 82002,
            "title": "一个旧讨论",
            "pub_time": "2026-09-25T00:00:00+00:00",
            "forum_id": 5,
            "snippet": "包含命中词的回复",
            "match_type": "floor",
            "matched_at": "floor",
        }
    ]


def test_non_discussion_forum_is_rejected(db):
    result = _call_with_db(db, query="测试", forum_ids=[30])
    assert result.ok is False
    assert result.error.code == "INVALID_FORUM_SCOPE"


def test_query_and_scope_limits_are_validated_before_database_access():
    result = find_discussions(query="x", tids=list(range(1, 27)))
    assert result.ok is False
    assert result.error.code == "INVALID_ARGUMENT"


def test_no_enabled_discussion_forums_returns_complete_empty_repository_shape(db):
    db.execute("UPDATE forums SET enabled = 0 WHERE content_kind = 'discussion'")

    result = _call_with_db(db, query="找不到的词")

    assert result.ok is True
    assert result.data["forum_ids"] == []
    assert result.data["body_search_status"] == "searched"
    assert result.data["result_status"] == "complete"
    assert result.data["budget"]["floor_match_limit_reached"] is False
    assert not result.warnings


def test_empty_and_invalid_date_values_return_invalid_argument():
    for date_value in ("", 123):
        result = find_discussions(query="测试", start_date=date_value)
        assert result.ok is False
        assert result.error.code == "INVALID_ARGUMENT"


def test_unavailable_body_search_is_not_reported_as_a_true_no_hit(db):
    result = _call_with_db(db, query="标题和正文都无命中")

    assert result.ok is True
    assert result.data["count"] == 0
    assert result.data["items"] == []
    assert result.data["body_search_status"] == "INDEX_UNAVAILABLE"
    assert result.data["result_status"] == "partial_index_unavailable"
    assert any("INDEX_UNAVAILABLE" in warning for warning in result.warnings)


def test_broad_two_character_search_returns_partial_title_hits_without_scanning_floors(db):
    _thread(db, tid=73001, title="百合短词标题", forum_id=5)
    _floor(db, pid=83001, tid=73001, floor_no=1, content="楼层也包含百合", pub_time="2026-09-25")

    result = _call_with_db(db, query="百合")

    assert result.ok is True
    assert result.data["body_search_status"] == "INDEX_UNAVAILABLE"
    assert result.data["result_status"] == "partial_index_unavailable"
    assert result.data["actual_mode"] == "title_only"
    assert [(item["tid"], item["match_type"], item["matched_at"]) for item in result.data["items"]] == [
        (73001, "title", "title")
    ]
    assert any("matching thread titles were searched" in warning for warning in result.warnings)


def test_short_broad_search_issues_bounded_title_query_but_skips_floor_query():
    class NoQueryConnection:
        backend = "sqlite"
        statements = []

        def execute(self, statement, *_args, **_kwargs):
            self.statements.append(statement)
            class EmptyRows:
                def fetchall(self):
                    return []
            return EmptyRows()

    conn = NoQueryConnection()
    result = DiscussionSearchRepository(conn).search(
        query="短词",
        forum_ids=[5],
        tids=[],
        start_at=None,
        end_at=None,
        limit=10,
        floor_match_limit=100,
    )

    assert result["body_search_status"] == "INDEX_UNAVAILABLE"
    assert result["items"] == []
    assert result["thread_rows_scanned"] == 0
    assert any("FROM threads t" in statement and "LIKE" in statement for statement in conn.statements)
    assert not any("JOIN floors" in statement for statement in conn.statements)


def test_broad_search_fails_closed_when_postgres_trigram_index_is_not_valid():
    class MissingIndexConnection:
        backend = "postgres"

        def execute(self, *_args, **_kwargs):
            class MissingIndex:
                def fetchone(self):
                    return None

            return MissingIndex()

    repo = DiscussionSearchRepository(MissingIndexConnection())

    assert repo._body_search_status(query="三字索引词", tids=[]) == "INDEX_UNAVAILABLE"
