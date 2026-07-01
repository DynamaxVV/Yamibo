"""Unit tests for discussion report generator (Step 05)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.application.discussion_report import (
    create_discussion_trend_report_job,
    create_forum_research_report_job,
    generate_trend_report,
    generate_forum_research_report,
)


def _fake_run(**overrides):
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


# -- generate_trend_report --


def test_generate_trend_report_json_sections():
    """Trend report JSON must include all required sections."""
    repo = MagicMock()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    repo.get_topic_daily_with_labels.return_value = _fake_topic_daily()
    repo.get_user_daily.return_value = _fake_user_daily()
    run = _fake_run()

    report_json, markdown = generate_trend_report(
        repo=repo,
        run=run,
        forum_id=5,
        start_date="2014-11-01",
        end_date="2014-11-30",
        version="trend-v1",
        period="monthly",
    )

    assert "metadata" in report_json
    assert report_json["metadata"]["forum_id"] == 5
    assert report_json["metadata"]["report_kind"] == "trend_report"
    assert "coverage" in report_json
    assert "partition_summary" in report_json
    assert "topic_summary" in report_json
    assert "user_summary" in report_json
    assert "warnings" in report_json
    assert "data_notes" in report_json

    # Partition summary
    ps = report_json["partition_summary"]
    assert ps["total_days"] == 2
    assert ps["total_post_count"] == 32
    assert ps["total_thread_count"] == 8
    assert ps["peak_day"]["bucket_date"] == "2014-11-01"

    # Topic summary
    ts = report_json["topic_summary"]
    assert len(ts["topics"]) == 2
    assert ts["topics"][0]["topic_label"] == "百合动画"
    assert ts["degraded"] is False

    # User summary
    us = report_json["user_summary"]
    assert us["total_active_users"] == 2
    assert len(us["top_users"]) == 2

    # Markdown
    assert "论坛趋势报告" in markdown
    assert "百合动画" in markdown
    assert "2014-11-01" in markdown


def test_generate_trend_report_topic_insufficient_warning():
    """When run has TOPIC_QUALITY_INSUFFICIENT warning, report reflects it."""
    repo = MagicMock()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    repo.get_topic_daily_with_labels.return_value = []
    repo.get_user_daily.return_value = _fake_user_daily()
    run = _fake_run(warnings_json=["TOPIC_QUALITY_INSUFFICIENT"])

    report_json, markdown = generate_trend_report(
        repo=repo, run=run,
        forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        version="trend-v1", period="monthly",
    )

    assert report_json["topic_summary"]["degraded"] is True
    assert report_json["topic_summary"]["degraded_note"] is not None
    assert "TOPIC_QUALITY_INSUFFICIENT" in report_json["warnings"]
    assert "降级说明" in markdown


def test_generate_trend_report_markdown_sections():
    """Markdown output must include all expected sections."""
    repo = MagicMock()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    repo.get_topic_daily_with_labels.return_value = _fake_topic_daily()
    repo.get_user_daily.return_value = _fake_user_daily()
    run = _fake_run()

    _, markdown = generate_trend_report(
        repo=repo, run=run,
        forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        version="trend-v1", period="monthly",
    )

    assert "## 数据覆盖" in markdown
    assert "## 分区活跃趋势" in markdown
    assert "## Topic 趋势" in markdown
    assert "## 用户活跃变化" in markdown
    assert "## 数据说明" in markdown


# -- generate_forum_research_report --


def test_generate_forum_research_report_no_run():
    """Forum research report works without a current run."""
    conn = MagicMock()
    evidence = AgentResult(
        ok=True,
        data={
            "items": [
                {"tid": 100, "pid": 200, "floor_no": 3, "display_title": "测试帖",
                 "publisher": "test_user", "pub_time": "2014-11-10T12:00:00+08:00",
                 "snippet": "这是关于海域区讨论氛围的一个例子。", "source_uri": "yamibo://threads/100#floor-3"},
            ]
        },
    )

    with patch("yamibo_mcp.application.discussion_report.get_forum_evidence_pack", return_value=evidence):
        report_json, markdown = generate_forum_research_report(
            repo=None,
            forum_id=33,
            start_date="2014-11-01",
            end_date="2014-11-30",
            question="海域区这个时期的讨论氛围如何？",
            intent="community_atmosphere",
            evidence_query="海域区 氛围",
            run=None,
            conn=conn,
        )

    assert report_json["metadata"]["report_kind"] == "forum_research"
    assert report_json["question"] == "海域区这个时期的讨论氛围如何？"
    assert report_json["intent"] == "community_atmosphere"
    assert report_json["trend_context"] is None
    assert len(report_json["evidence_summary"]["items"]) == 1
    assert report_json["evidence_summary"]["items"][0]["snippet"] is not None

    assert "论坛研究报告" in markdown
    assert "海域区这个时期的讨论氛围如何？" in markdown


def test_generate_forum_research_report_with_run_context():
    """When a current run exists, include trend context."""
    conn = MagicMock()
    repo = MagicMock()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    run = _fake_run()
    evidence = AgentResult(ok=True, data={"items": []})

    with patch("yamibo_mcp.application.discussion_report.get_forum_evidence_pack", return_value=evidence):
        report_json, markdown = generate_forum_research_report(
            repo=repo,
            forum_id=33,
            start_date="2014-11-01",
            end_date="2014-11-30",
            question="测试问题",
            intent="general_research",
            evidence_query="测试",
            run=run,
            conn=conn,
        )

    assert report_json["trend_context"] is not None
    assert report_json["trend_context"]["total_post_count"] == 32
    assert report_json["coverage"]["has_trend_context"] is True
    assert "EVIDENCE_INSUFFICIENT" in report_json["warnings"]


def test_generate_forum_research_report_evidence_insufficient_warning():
    """When no evidence found, add EVIDENCE_INSUFFICIENT warning."""
    conn = MagicMock()
    evidence = AgentResult(ok=True, data={"items": []})

    with patch("yamibo_mcp.application.discussion_report.get_forum_evidence_pack", return_value=evidence):
        report_json, markdown = generate_forum_research_report(
            repo=None,
            forum_id=33,
            start_date="2014-11-01",
            end_date="2014-11-30",
            question="无结果查询",
            intent="slang_usage",
            evidence_query="不存在的关键词",
            run=None,
            conn=conn,
        )

    assert "EVIDENCE_INSUFFICIENT" in report_json["warnings"]
    assert "未检索到" in markdown or "未找到匹配证据" in markdown


# -- job creation commands --


def _setup_job_creation_mocks():
    """Patch load_settings, connect, and JobsRepository.create for job creation tests."""
    settings = MagicMock()
    settings.return_value.db_path = "/tmp/fake.db"
    fake_job = MagicMock()
    fake_job.job_id = "job-abc-123"
    conn = MagicMock()
    jobs_repo = MagicMock()
    jobs_repo.create.return_value = fake_job
    return (
        patch("yamibo_mcp.application.discussion_report.load_settings", settings),
        patch("yamibo_mcp.application.discussion_report.connect", return_value=conn),
        patch("yamibo_mcp.application.discussion_report.JobsRepository", return_value=jobs_repo),
    )


def test_create_trend_report_job_success():
    with patch("yamibo_mcp.application.discussion_report.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_report.connect") as connect_fn, \
         patch("yamibo_mcp.application.discussion_report.JobsRepository") as jobs_cls:
        settings.return_value.db_path = "/tmp/fake.db"
        fake_job = MagicMock()
        fake_job.job_id = "job-trend-001"
        jobs_cls.return_value.create.return_value = fake_job

        result = create_discussion_trend_report_job(
            forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        )

    assert result.ok is True
    assert result.data["job_id"] == "job-trend-001"
    assert result.data["report_kind"] == "trend_report"
    jobs_cls.return_value.create.assert_called_once()


def test_create_trend_report_job_invalid_forum_id():
    result = create_discussion_trend_report_job(
        forum_id=-1, start_date="2014-11-01", end_date="2014-11-30",
    )
    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_INVALID_PAYLOAD"


def test_create_trend_report_job_invalid_period():
    result = create_discussion_trend_report_job(
        forum_id=5, start_date="2014-11-01", end_date="2014-11-30",
        period="yearly",
    )
    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_INVALID_PAYLOAD"


def test_create_forum_research_report_job_success():
    with patch("yamibo_mcp.application.discussion_report.load_settings") as settings, \
         patch("yamibo_mcp.application.discussion_report.connect") as connect_fn, \
         patch("yamibo_mcp.application.discussion_report.JobsRepository") as jobs_cls:
        settings.return_value.db_path = "/tmp/fake.db"
        fake_job = MagicMock()
        fake_job.job_id = "job-research-001"
        jobs_cls.return_value.create.return_value = fake_job

        result = create_forum_research_report_job(
            forum_id=33, start_date="2014-11-01", end_date="2014-11-30",
            question="海域区这个时期的讨论氛围如何？",
            intent="community_atmosphere",
        )

    assert result.ok is True
    assert result.data["job_id"] == "job-research-001"
    assert result.data["report_kind"] == "forum_research"
    jobs_cls.return_value.create.assert_called_once()


def test_create_forum_research_report_job_empty_question():
    result = create_forum_research_report_job(
        forum_id=33, start_date="2014-11-01", end_date="2014-11-30",
        question="",
    )
    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_INVALID_PAYLOAD"


def test_create_forum_research_report_job_invalid_intent():
    result = create_forum_research_report_job(
        forum_id=33, start_date="2014-11-01", end_date="2014-11-30",
        question="测试", intent="bad_intent",
    )
    assert result.ok is False
    assert result.error.code == "DISCUSSION_TREND_INVALID_PAYLOAD"


# -- CLI parse smoke --


def test_cli_create_trend_report_job_parses():
    from yamibo_mcp.server.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "create-discussion-trend-report-job",
        "--forum-id", "5",
        "--start-date", "2014-11-01",
        "--end-date", "2014-11-30",
        "--period", "monthly",
    ])
    assert args.command == "create-discussion-trend-report-job"
    assert args.forum_id == 5
    assert args.period == "monthly"


def test_cli_create_forum_research_report_job_parses():
    from yamibo_mcp.server.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "create-forum-research-report-job",
        "--forum-id", "33",
        "--start-date", "2014-11-01",
        "--end-date", "2014-11-30",
        "--question", "海域区氛围如何？",
        "--intent", "community_atmosphere",
    ])
    assert args.command == "create-forum-research-report-job"
    assert args.forum_id == 33
    assert args.question == "海域区氛围如何？"
    assert args.intent == "community_atmosphere"


# -- agent tool registration --


def test_new_report_tools_registered():
    """Verify the two new report tools are in PUBLIC_AGENT_TOOLS."""
    from yamibo_mcp.server.agent_tools import PUBLIC_AGENT_TOOLS

    names = {n for n, _, _ in PUBLIC_AGENT_TOOLS}
    assert "create_discussion_trend_report_job" in names
    assert "create_forum_research_report_job" in names


def test_trend_report_includes_evidence_when_available():
    """Trend report with evidence adds evidence section and partial warning."""
    repo = MagicMock()
    run = _fake_run()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    repo.get_topic_daily_with_labels.return_value = _fake_topic_daily()
    repo.get_user_daily.return_value = _fake_user_daily()
    # Only return evidence for the first topic → partial
    repo.get_topic_floor_evidence.side_effect = lambda **kw: (
        [{"tid": 100, "pid": 200, "floor_no": 3,
          "display_title": "测试帖", "publisher": "test_user",
          "pub_time": "2014-11-10T12:00:00+08:00",
          "snippet": "讨论内容", "source_uri": "yamibo://threads/100#floor=3",
          "score": 0.0, "evidence_source": "sql"}]
        if kw.get("topic_id") == 1 else []
    )
    conn = MagicMock()
    fake_chunks = MagicMock()
    fake_chunks.keyword_search.return_value = []

    with patch("yamibo_mcp.application.discussion_report.RagChunksRepository", return_value=fake_chunks):
        report_json, markdown = generate_trend_report(
            repo=repo,
            run=run,
            forum_id=5,
            start_date="2014-11-01",
            end_date="2014-11-30",
            version="trend-v1",
            period="monthly",
            conn=conn,
        )

    assert "evidence" in report_json
    assert "topics" in report_json["evidence"]
    assert len(report_json["evidence"]["topics"]) == 1  # only topic 1 has evidence
    assert "DISCUSSION_EVIDENCE_PARTIAL" in report_json["warnings"]
    # Markdown should include evidence section
    assert "代表性证据片段" in markdown


def test_trend_report_without_conn_skips_evidence():
    """When conn is None, evidence section is omitted without error."""
    repo = MagicMock()
    run = _fake_run()
    repo.get_partition_daily.return_value = _fake_partition_daily()
    repo.get_topic_daily_with_labels.return_value = _fake_topic_daily()
    repo.get_user_daily.return_value = _fake_user_daily()

    report_json, markdown = generate_trend_report(
        repo=repo,
        run=run,
        forum_id=5,
        start_date="2014-11-01",
        end_date="2014-11-30",
        version="trend-v1",
        period="monthly",
        conn=None,
    )

    assert "evidence" not in report_json
    assert "代表性证据片段" not in markdown
