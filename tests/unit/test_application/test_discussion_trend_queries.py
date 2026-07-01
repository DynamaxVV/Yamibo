"""Unit tests for discussion trend queries (Step 04)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yamibo_mcp.application.discussion_trend_queries import (
    get_discussion_partition_trends,
    get_discussion_topic_trends,
    get_discussion_user_trends,
    get_discussion_report,
    get_discussion_topic_evidence,
    get_forum_evidence_pack,
)


def _fake_run(**overrides):
    """Minimal fake DiscussionIndexRun for query tests."""
    from yamibo_mcp.db.repositories.discussion_trends import DiscussionIndexRun

    defaults = {
        "run_id": "run-001",
        "forum_id": 5,
        "start_date": "2014-11-01",
        "end_date": "2014-11-30",
        "version": "trend-v1",
        "status": "succeeded",
        "started_at": "2014-11-01T00:00:00+08:00",
        "completed_at": "2014-11-30T23:59:59+08:00",
        "superseded_at": None,
        "metrics_json": {},
        "warnings_json": [],
        "error_json": {},
        "created_at": "2014-11-01T00:00:00+08:00",
        "updated_at": "2014-11-30T23:59:59+08:00",
    }
    defaults.update(overrides)
    return DiscussionIndexRun(**defaults)


def _fake_partition_daily():
    return [
        {"bucket_date": "2014-11-01", "thread_count": 5, "post_count": 20,
         "active_user_count": 8, "new_thread_count": 2, "reply_count": 15,
         "metrics_json": {}},
        {"bucket_date": "2014-11-02", "thread_count": 3, "post_count": 12,
         "active_user_count": 6, "new_thread_count": 1, "reply_count": 9,
         "metrics_json": {}},
    ]


def _fake_topic_daily():
    return [
        {"topic_id": 1, "topic_key": "topic:yuri_anime", "topic_label": "百合动画",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.9,
         "bucket_date": "2014-11-01", "thread_count": 3, "post_count": 15,
         "active_user_count": 5, "assignment_count": 8, "evidence_count": 0,
         "metrics_json": {}},
        {"topic_id": 1, "topic_key": "topic:yuri_anime", "topic_label": "百合动画",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.9,
         "bucket_date": "2014-11-02", "thread_count": 2, "post_count": 10,
         "active_user_count": 4, "assignment_count": 5, "evidence_count": 0,
         "metrics_json": {}},
        {"topic_id": 2, "topic_key": "topic:light_novel", "topic_label": "轻小说",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.85,
         "bucket_date": "2014-11-01", "thread_count": 1, "post_count": 5,
         "active_user_count": 3, "assignment_count": 3, "evidence_count": 0,
         "metrics_json": {}},
        {"topic_id": 2, "topic_key": "topic:light_novel", "topic_label": "轻小说",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.85,
         "bucket_date": "2014-11-02", "thread_count": 1, "post_count": 4,
         "active_user_count": 2, "assignment_count": 2, "evidence_count": 0,
         "metrics_json": {}},
    ]


def _fake_user_daily():
    return [
        {"user_key": "user_a", "display_name": "User A",
         "bucket_date": "2014-11-01", "thread_count": 3, "post_count": 10,
         "topic_count": 2, "metrics_json": {}},
        {"user_key": "user_a", "display_name": "User A",
         "bucket_date": "2014-11-02", "thread_count": 2, "post_count": 8,
         "topic_count": 1, "metrics_json": {}},
        {"user_key": "user_b", "display_name": "User B",
         "bucket_date": "2014-11-01", "thread_count": 2, "post_count": 6,
         "topic_count": 1, "metrics_json": {}},
    ]


def _setup_query_mocks(fake_repo, fake_conn):
    """Patch the three globals used by all query functions."""
    settings = MagicMock()
    settings.return_value.db_path = "/tmp/fake.db"
    return (
        patch("yamibo_mcp.application.discussion_trend_queries.load_settings", settings),
        patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=fake_conn),
        patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=fake_repo),
    )


# --- no current run ---


def test_partition_trends_no_current_run_returns_error():
    repo = MagicMock()
    repo.get_current_run.return_value = None
    conn = MagicMock()
    conn.backend = "postgres"

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_partition_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "DISCUSSION_TREND_NO_CURRENT_RUN"
    assert result.error.retryable is True
    conn.close.assert_called_once()


def test_topic_trends_no_current_run_returns_error():
    repo = MagicMock()
    repo.get_current_run.return_value = None
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_NO_CURRENT_RUN"


def test_run_not_succeeded_returns_error():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run(status="running")
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_partition_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_RUN_NOT_SUCCEEDED"


# --- partition trends ---


def test_partition_trends_returns_daily_buckets():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_partition_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is True
    assert result.data["granularity"] == "day"
    assert len(result.data["buckets"]) == 2
    assert result.data["buckets"][0]["bucket_date"] == "2014-11-01"
    assert result.data["buckets"][0]["thread_count"] == 5
    assert result.data["forum_id"] == 5
    assert result.data["run_id"] == "run-001"


def test_partition_trends_monthly_aggregation():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_partition_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            granularity="month",
        )

    assert result.ok is True
    assert result.data["granularity"] == "month"
    assert len(result.data["buckets"]) == 1
    assert result.data["buckets"][0]["bucket_date"] == "2014-11"
    # Summed across two days: 5+3=8, 20+12=32
    assert result.data["buckets"][0]["thread_count"] == 8
    assert result.data["buckets"][0]["post_count"] == 32


def test_partition_trends_invalid_granularity():
    result = get_discussion_partition_trends(
        forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        granularity="week",
    )
    assert result.ok is False
    assert result.error.code == "INVALID_ARGUMENT"


# --- topic trends ---


def test_topic_trends_returns_ranked_topics():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_topic_daily_with_labels.return_value = _fake_topic_daily()
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is True
    assert len(result.data["topics"]) == 2
    # Sorted by total_assignment_count desc: topic 1 has 13, topic 2 has 5
    assert result.data["topics"][0]["topic_key"] == "topic:yuri_anime"
    assert result.data["topics"][0]["total_thread_count"] == 5
    assert result.data["topics"][0]["total_post_count"] == 25
    assert len(result.data["topics"][0]["daily"]) == 2


def test_topic_trends_min_thread_count_filter():
    """With min_thread_count=2, both topics pass; with =4, only 1 passes → quality insufficient."""
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_topic_daily_with_labels.return_value = _fake_topic_daily()
    conn = MagicMock()

    # min_thread_count=2: both topics qualify
    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            min_thread_count=2,
        )

    assert result.ok is True
    assert len(result.data["topics"]) == 2

    # min_thread_count=4: only topic 1 qualifies → < 2 → quality insufficient
    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result2 = get_discussion_topic_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            min_thread_count=4,
        )

    assert result2.ok is True
    assert result2.data["topics"] == []
    assert "TOPIC_QUALITY_INSUFFICIENT" in result2.warnings


def test_topic_trends_quality_insufficient():
    """When filtered topics < 2, return empty + TOPIC_QUALITY_INSUFFICIENT."""
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    # Only one topic with low counts
    sparse = [
        {"topic_id": 1, "topic_key": "topic:x", "topic_label": "X",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.5,
         "bucket_date": "2014-11-01", "thread_count": 1, "post_count": 1,
         "active_user_count": 1, "assignment_count": 1, "evidence_count": 0,
         "metrics_json": {}},
    ]
    repo.get_topic_daily_with_labels.return_value = sparse
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is True
    assert result.data["topics"] == []
    assert "TOPIC_QUALITY_INSUFFICIENT" in result.warnings


# --- user trends ---


def test_user_trends_returns_ranked_users():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_user_daily.return_value = _fake_user_daily()
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_user_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is True
    assert len(result.data["users"]) == 2
    # Sorted by post_count desc: user_a=18, user_b=6
    assert result.data["users"][0]["user_key"] == "user_a"
    assert result.data["users"][0]["total_post_count"] == 18
    assert len(result.data["users"][0]["daily"]) == 2
    assert result.data["sort_by"] == "post_count"


def test_user_trends_sort_by_thread_count():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_user_daily.return_value = _fake_user_daily()
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_user_trends(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            sort_by="thread_count",
        )

    assert result.ok is True
    assert result.data["sort_by"] == "thread_count"
    # user_a total_thread_count=5, user_b=2
    assert result.data["users"][0]["user_key"] == "user_a"


def test_user_trends_invalid_sort_by():
    result = get_discussion_user_trends(
        forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        sort_by="invalid_field",
    )
    assert result.ok is False
    assert result.error.code == "INVALID_ARGUMENT"


# --- report ---


def test_get_report_not_found():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_report_runs.return_value = []
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_report(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_REPORT_NOT_FOUND"
    assert result.error.retryable is True


# --- forum evidence pack ---


def test_forum_evidence_pack_sql_fallback():
    """Evidence pack should work without a current run."""
    conn = MagicMock()
    conn.backend = "postgres"
    repo = MagicMock()
    repo.search_forum_floor_snippets.return_value = []

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo), \
         patch("yamibo_mcp.application.discussion_trend_queries.RagChunksRepository") as fake_chunks_cls:
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_forum_evidence_pack(
            forum_id=33, query="黑话测试",
        )

    assert result.ok is True
    assert result.data["forum_id"] == 33
    assert result.data["query"] == "黑话测试"
    assert result.data["intent"] == "general_research"
    assert result.data["items"] == []
    fake_chunks_cls.return_value.keyword_search.assert_called_once_with(
        query="黑话测试", top_k=20, forum_id=33,
    )
    repo.search_forum_floor_snippets.assert_called_once_with(
        forum_id=33,
        query="黑话测试",
        top_k=10,
        start_date=None,
        end_date=None,
    )


def test_forum_evidence_pack_non_postgres_returns_error():
    conn = MagicMock()
    conn.backend = "sqlite"

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_forum_evidence_pack(
            forum_id=33, query="test",
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_POSTGRES_REQUIRED"


def test_forum_evidence_pack_rag_mode_unavailable():
    """mode=rag returns DISCUSSION_RAG_EVIDENCE_UNAVAILABLE when RAG fails."""
    conn = MagicMock()
    conn.backend = "postgres"
    fake_chunks_repo = MagicMock()
    fake_chunks_repo.keyword_search.return_value = []

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.RagChunksRepository", return_value=fake_chunks_repo):
        settings.return_value.db_path = "/tmp/fake.db"
        settings.return_value.rag_enabled = True
        result = get_forum_evidence_pack(
            forum_id=33, query="test", mode="rag",
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_RAG_EVIDENCE_UNAVAILABLE"


def test_forum_evidence_pack_invalid_mode():
    result = get_forum_evidence_pack(forum_id=33, query="test", mode="invalid")
    assert result.ok is False
    assert result.error.code == "INVALID_ARGUMENT"


def test_forum_evidence_pack_invalid_intent():
    result = get_forum_evidence_pack(forum_id=33, query="test", intent="bad_intent")
    assert result.ok is False
    assert result.error.code == "INVALID_ARGUMENT"


def test_forum_evidence_pack_with_current_run_required():
    """When require_current_run=True, validates run exists."""
    conn = MagicMock()
    conn.backend = "postgres"
    repo = MagicMock()
    repo.get_current_run.return_value = None

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_forum_evidence_pack(
            forum_id=33, query="test",
            start_date="2014-11-01", end_date="2014-11-30",
            require_current_run=True,
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_NO_CURRENT_RUN"


# --- agent tool wrapper wire format ---


def test_agent_tool_wrapper_returns_wire_json():
    """Agent tool wrappers should return dict (wire format), not AgentResult."""
    from yamibo_mcp.server.agent_tools import get_discussion_partition_trends as tool_fn
    from yamibo_mcp.server.agent_adapter import to_wire

    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    conn = MagicMock()

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        wire = tool_fn(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert isinstance(wire, dict)
    assert wire["ok"] is True
    assert "data" in wire
    assert wire["data"]["forum_id"] == 5


def test_all_discussion_tools_registered():
    """Verify all 6 discussion tools are in PUBLIC_AGENT_TOOLS."""
    from yamibo_mcp.server.agent_tools import PUBLIC_AGENT_TOOLS

    names = {n for n, _, _ in PUBLIC_AGENT_TOOLS}
    expected = {
        "create_discussion_trend_index_job",
        "get_discussion_partition_trends",
        "get_discussion_topic_trends",
        "get_discussion_user_trends",
        "get_discussion_report",
        "get_discussion_topic_evidence",
        "get_forum_evidence_pack",
        "create_discussion_trend_report_job",
        "create_forum_research_report_job",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"


# --- CLI parse smoke ---


def test_cli_discussion_partition_trends_parses():
    from yamibo_mcp.server.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "discussion-partition-trends",
        "--forum-id", "5",
        "--start-date", "2014-11-01",
        "--end-date", "2014-11-30",
    ])
    assert args.command == "discussion-partition-trends"
    assert args.forum_id == 5
    assert args.start_date == "2014-11-01"


def test_cli_forum_evidence_pack_parses():
    from yamibo_mcp.server.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "forum-evidence-pack",
        "--forum-id", "33",
        "--query", "黑话",
        "--mode", "sql",
        "--intent", "slang_usage",
    ])
    assert args.command == "forum-evidence-pack"
    assert args.forum_id == 33
    assert args.query == "黑话"
    assert args.mode == "sql"
    assert args.intent == "slang_usage"


# --- get_discussion_topic_evidence ---


def test_topic_evidence_no_current_run_returns_error():
    repo = MagicMock()
    repo.get_current_run.return_value = None
    conn = MagicMock()
    conn.backend = "postgres"

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_evidence(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            topic_id=1,
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_NO_CURRENT_RUN"


def test_topic_evidence_topic_not_found():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_topics_for_run.return_value = [
        {"topic_id": 999, "topic_key": "topic:other", "topic_label": "其他",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.5,
         "metadata_json": {}},
    ]
    conn = MagicMock()
    conn.backend = "postgres"

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_evidence(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            topic_id=1,
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_TOPIC_NOT_FOUND"
    assert result.error.retryable is False


def test_topic_evidence_sql_fallback():
    """SQL fallback returns evidence with required fields."""
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_topics_for_run.return_value = [
        {"topic_id": 1, "topic_key": "topic:yuri_anime", "topic_label": "百合动画",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.9,
         "metadata_json": {}},
    ]
    repo.get_topic_floor_evidence.return_value = [
        {"tid": 100, "pid": 200, "floor_no": 3,
         "display_title": "测试帖", "publisher": "test_user",
         "pub_time": "2014-11-10T12:00:00+08:00",
         "snippet": "讨论内容片段", "source_uri": "yamibo://threads/100#floor=3",
         "score": 0.0, "evidence_source": "sql"},
    ]
    conn = MagicMock()
    conn.backend = "postgres"

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_evidence(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            topic_id=1, mode="sql",
        )

    assert result.ok is True
    assert result.data["retrieval_mode"] == "sql"
    assert result.data["topic_id"] == 1
    assert result.data["topic_label"] == "百合动画"
    assert len(result.data["items"]) == 1
    item = result.data["items"][0]
    assert item["tid"] == 100
    assert item["source_uri"] == "yamibo://threads/100#floor=3"
    assert len(item["snippet"]) <= 220


def test_topic_evidence_auto_fallback_emits_stable_warning_code():
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_topics_for_run.return_value = [
        {"topic_id": 1, "topic_key": "topic:yuri_anime", "topic_label": "百合动画",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.9,
         "metadata_json": {}},
    ]
    repo.get_topic_floor_evidence.return_value = []
    conn = MagicMock()
    conn.backend = "postgres"
    fake_chunks = MagicMock()
    fake_chunks.keyword_search.return_value = []

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo), \
         patch("yamibo_mcp.application.discussion_trend_queries.RagChunksRepository", return_value=fake_chunks):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_evidence(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            topic_id=1, mode="auto",
        )

    assert result.ok is True
    assert "DISCUSSION_RAG_SQL_FALLBACK_USED" in result.warnings
    assert result.data["retrieval_diagnostics"]["sql_fallback_used"] is True


def test_topic_evidence_requires_topic_id_or_label():
    result = get_discussion_topic_evidence(
        forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
    )
    assert result.ok is False
    assert result.error.code == "INVALID_ARGUMENT"


def test_topic_evidence_rag_mode_unavailable():
    """mode=rag with RAG unavailable returns DISCUSSION_RAG_EVIDENCE_UNAVAILABLE."""
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_topics_for_run.return_value = [
        {"topic_id": 1, "topic_key": "topic:yuri_anime", "topic_label": "百合动画",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.9,
         "metadata_json": {}},
    ]
    conn = MagicMock()
    conn.backend = "postgres"
    fake_chunks = MagicMock()
    fake_chunks.keyword_search.return_value = []

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo), \
         patch("yamibo_mcp.application.discussion_trend_queries.RagChunksRepository", return_value=fake_chunks):
        settings.return_value.db_path = "/tmp/fake.db"
        settings.return_value.rag_enabled = True
        result = get_discussion_topic_evidence(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            topic_id=1, mode="rag",
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_RAG_EVIDENCE_UNAVAILABLE"
    assert result.error.retryable is True


def test_topic_evidence_by_label():
    """Topic resolution by label works."""
    repo = MagicMock()
    repo.get_current_run.return_value = _fake_run()
    repo.get_topics_for_run.return_value = [
        {"topic_id": 2, "topic_key": "topic:light_novel", "topic_label": "轻小说",
         "topic_kind": "category", "topic_source": "category_tag", "confidence": 0.85,
         "metadata_json": {}},
    ]
    repo.get_topic_floor_evidence.return_value = []
    conn = MagicMock()
    conn.backend = "postgres"

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_evidence(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            topic_label="轻小说", mode="sql",
        )

    assert result.ok is True
    assert result.data["topic_id"] == 2


def test_topic_evidence_non_postgres_returns_error():
    conn = MagicMock()
    conn.backend = "sqlite"

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_discussion_topic_evidence(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
            topic_id=1,
        )

    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_POSTGRES_REQUIRED"


# --- thread diversity ---


def test_enforce_thread_diversity():
    """max 3 items per thread."""
    from yamibo_mcp.application.discussion_trend_queries import _enforce_thread_diversity

    items = [
        {"tid": 1, "text": "a"}, {"tid": 1, "text": "b"},
        {"tid": 1, "text": "c"}, {"tid": 1, "text": "d"},
        {"tid": 2, "text": "e"}, {"tid": 2, "text": "f"},
    ]
    result = _enforce_thread_diversity(items, max_per_tid=3)
    assert len(result) == 5
    tids = [it["tid"] for it in result]
    assert tids.count(1) == 3
    assert tids.count(2) == 2


# --- evidence pack RAG/hybrid ---


def test_forum_evidence_pack_includes_retrieval_metadata():
    """Evidence pack result includes retrieval_mode, diversity, limitations."""
    conn = MagicMock()
    conn.backend = "postgres"
    fake_chunks = MagicMock()
    fake_chunks.keyword_search.return_value = []
    repo = MagicMock()
    repo.search_forum_floor_snippets.return_value = []

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.RagChunksRepository", return_value=fake_chunks), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_forum_evidence_pack(
            forum_id=33, query="测试", mode="sql",
        )

    assert result.ok is True
    assert "retrieval_mode" in result.data
    assert "query_terms" in result.data
    assert "diversity" in result.data
    assert "limitations" in result.data
    assert "retrieval_diagnostics" in result.data
    assert result.data["diversity"]["max_per_tid"] == 3
    assert isinstance(result.data["limitations"], list)


def test_forum_evidence_pack_auto_fallback_emits_stable_warning_code():
    conn = MagicMock()
    conn.backend = "postgres"
    fake_chunks = MagicMock()
    fake_chunks.keyword_search.return_value = []
    repo = MagicMock()
    repo.search_forum_floor_snippets.return_value = []

    with patch("yamibo_mcp.application.discussion_trend_queries.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_trend_queries.connect", return_value=conn), \
         patch("yamibo_mcp.application.discussion_trend_queries.RagChunksRepository", return_value=fake_chunks), \
         patch("yamibo_mcp.application.discussion_trend_queries.DiscussionTrendRepository", return_value=repo):
        settings.return_value.db_path = "/tmp/fake.db"
        result = get_forum_evidence_pack(
            forum_id=33, query="测试", mode="auto",
        )

    assert result.ok is True
    assert "DISCUSSION_RAG_SQL_FALLBACK_USED" in result.warnings
    assert result.data["retrieval_diagnostics"]["sql_fallback_used"] is True


def test_cli_discussion_topic_evidence_parses():
    from yamibo_mcp.server.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "discussion-topic-evidence",
        "--forum-id", "5",
        "--start-date", "2014-11-01",
        "--end-date", "2014-11-30",
        "--topic-label", "百合动画",
        "--mode", "auto",
    ])
    assert args.command == "discussion-topic-evidence"
    assert args.forum_id == 5
    assert args.topic_label == "百合动画"
    assert args.mode == "auto"
