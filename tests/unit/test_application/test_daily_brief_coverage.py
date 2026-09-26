from datetime import date, datetime, timezone

import pytest

from yamibo_mcp.application import daily_brief_coverage as coverage


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def execute(self, *_args, **_kwargs):
        return _Rows([{"forum_id": 5}, {"forum_id": 13}])


class _BriefRepo:
    saved = None

    def __init__(self, _conn):
        pass

    def start_attempt(self, *, issue_id):
        assert issue_id == "issue-1"
        return {"attempt_id": "attempt-1"}

    def finish_attempt(self, *, attempt_id, status, coverage):
        self.__class__.saved = (attempt_id, status, coverage)


class _Job:
    def __init__(self, status="succeeded", artifacts=None):
        self.status = status
        self.artifacts = artifacts if artifacts is not None else {
            "pages_fetched": 2,
            "total_pages_detected": 2,
            "fetch_stopped_reason": "last_page",
            "archive_status": "complete",
        }


class _JobsRepo:
    status = "succeeded"

    def __init__(self, _conn):
        pass

    def get(self, _job_id):
        return _Job(self.status)


def test_scan_continues_page_by_page_and_old_thread_reply_becomes_candidate(monkeypatch):
    monkeypatch.setattr(coverage, "DailyBriefsRepository", _BriefRepo)
    monkeypatch.setattr(coverage, "JobsRepository", _JobsRepo)
    monkeypatch.setattr(coverage, "_thread_floor_evidence", lambda *_args: {
        "eligible_discussion": True,
        "archive_status": "complete",
        "capture_mode": "text_only",
        "floor_count": 3,
        "local_reply_count_consistent": True,
        "target_day_floor_count": 1,
        "oldest_floor_at": "2026-09-22T10:00:00+00:00",
        "newest_floor_at": "2026-09-24T04:00:00+00:00",
    })
    calls = []

    def browse_page(*, page, forum_id, order):
        calls.append((forum_id, page, order))
        if page == 1:
            items = [{"tid": 101, "posted_at": "2026-08-01 12:00:00", "last_reply_at": "2026-09-24 12:00:00"}]
        else:
            items = [{"tid": 102, "posted_at": "2026-09-24 10:00:00", "last_reply_at": "2026-09-24 10:00:00"}]
        return {"forum_url": f"https://forum/{forum_id}?page={page}", "total_pages": 2, "items": items}

    result = coverage.check_daily_brief_coverage(
        _Conn(), issue_id="issue-1", target_day=date(2026, 9, 24),
        forum_ids=[5], page_budget=4, archive_budget=1, browse_page=browse_page,
        enqueue_archive=lambda **_kwargs: {"job_id": "job-1", "status": "queued"},
        now=lambda: datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    assert calls == [(5, 1, "dateline"), (5, 2, "dateline"), (5, 1, "default"), (5, 2, "default")]
    assert result["coverage_status"] == "partial"
    assert result["complete"] is False
    assert result["pages_used"] == 4
    assert [item["page"] for item in result["forums"][0]["pages"]] == [1, 2]
    assert result["pages"][0]["url"] == "https://forum/5?page=1"
    assert result["pages"][0]["fetched_at"] == "2026-09-25T00:00:00+00:00"
    assert result["pages"][1]["stop_reason"] == "reached_reported_last_page"
    assert result["pages"][1]["target_day_start_utc"] == "2026-09-23T16:00:00+00:00"
    assert result["pages"][1]["pages_remaining_after_page"] == 2
    old_thread = next(item for item in result["candidates"] if item["tid"] == 101)
    assert old_thread["candidate_reasons"] == ["thread_replied"]
    assert old_thread["archive"]["status"] == "verified"
    assert old_thread["archive"]["job_page_evidence"]["complete"] is True
    unqueued = next(item for item in result["candidates"] if item["tid"] == 102)
    assert unqueued["archive"]["status"] == "not_queued_budget_exhausted"
    assert result["candidate_queue_count"] == 1
    assert result["candidate_queue_truncated"] is True
    assert _BriefRepo.saved[1] == "partial"
    assert _BriefRepo.saved[2]["full_forum_coverage_proven"] is False


def test_budget_or_missing_dates_stays_partial_with_explicit_stop_reason(monkeypatch):
    monkeypatch.setattr(coverage, "DailyBriefsRepository", _BriefRepo)
    calls = []

    def browse_page(*, page, forum_id, order):
        calls.append(page)
        return {
            "forum_url": "https://forum/5?page=1", "total_pages": 8,
            "items": [{"tid": 7, "posted_at": None, "last_reply_at": "not-a-date"}],
        }

    result = coverage.check_daily_brief_coverage(
        _Conn(), issue_id="issue-1", target_day=date(2026, 9, 24), forum_ids=[5],
        page_budget=1, browse_page=browse_page,
        enqueue_archive=lambda **_kwargs: {"job_id": "job-1", "status": "queued"},
        now=lambda: datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    assert calls == [1]
    assert result["coverage_status"] == "partial"
    assert result["forums"][0]["stop_reason"] == "page_budget_exhausted"
    assert result["pages"][0]["missing_posted_at"] == 1
    assert result["pages"][0]["missing_last_reply_at"] == 1
    assert result["candidates"] == []


def test_queued_candidate_job_is_not_reported_as_verified(monkeypatch):
    monkeypatch.setattr(coverage, "DailyBriefsRepository", _BriefRepo)
    monkeypatch.setattr(coverage, "JobsRepository", _JobsRepo)
    monkeypatch.setattr(_JobsRepo, "status", "queued")
    monkeypatch.setattr(coverage, "_thread_floor_evidence", lambda *_args: {
        "eligible_discussion": True,
        "archive_status": "complete",
        "floor_count": 2,
        "local_reply_count_consistent": True,
        "target_day_floor_count": 1,
    })
    result = coverage.check_daily_brief_coverage(
        _Conn(), issue_id="issue-1", target_day=date(2026, 9, 24), forum_ids=[5],
        page_budget=1,
        browse_page=lambda **_kwargs: {
            "forum_url": "https://forum/5?page=1", "total_pages": 1,
            "items": [{"tid": 77, "posted_at": "2026-09-24 10:00:00", "last_reply_at": "2026-09-24 10:00:00"}],
        },
        enqueue_archive=lambda **_kwargs: {"job_id": "job-pending", "status": "queued"},
        now=lambda: datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    archive = result["candidates"][0]["archive"]
    assert archive["job_status"] == "queued"
    assert archive["job_terminal"] is False
    assert archive["target_day_floor_evidence_valid"] is False


def test_succeeded_job_without_page_artifacts_cannot_prove_archive_completeness(monkeypatch):
    monkeypatch.setattr(coverage, "DailyBriefsRepository", _BriefRepo)
    monkeypatch.setattr(coverage, "JobsRepository", _JobsRepo)
    monkeypatch.setattr(_JobsRepo, "status", "succeeded")
    monkeypatch.setattr(coverage, "_thread_floor_evidence", lambda *_args: {
        "eligible_discussion": True,
        "archive_status": "complete",
        "floor_count": 2,
        "local_reply_count_consistent": True,
        "target_day_floor_count": 1,
    })
    monkeypatch.setattr(_JobsRepo, "get", lambda self, _job_id: _Job("succeeded", artifacts={}))

    result = coverage.check_daily_brief_coverage(
        _Conn(), issue_id="issue-1", target_day=date(2026, 9, 24), forum_ids=[5],
        page_budget=1,
        browse_page=lambda **_kwargs: {
            "forum_url": "https://forum/5?page=1", "total_pages": 1,
            "items": [{"tid": 78, "posted_at": "2026-09-24 10:00:00", "last_reply_at": "2026-09-24 10:00:00"}],
        },
        enqueue_archive=lambda **_kwargs: {"job_id": "job-succeeded", "status": "succeeded"},
        now=lambda: datetime(2026, 9, 25, tzinfo=timezone.utc),
    )

    archive = result["candidates"][0]["archive"]
    assert archive["job_terminal"] is True
    assert archive["job_page_evidence"]["complete"] is False
    assert archive["status"] == "not_verifiable"
    assert archive["target_day_floor_evidence_valid"] is False


def test_page_and_archive_budgets_have_hard_upper_bounds():
    for kwargs, message in [
        ({"page_budget": coverage.MAX_PAGE_BUDGET + 1}, "page_budget"),
        ({"archive_budget": coverage.MAX_ARCHIVE_BUDGET + 1}, "archive_budget"),
    ]:
        with pytest.raises(ValueError, match=message):
            coverage.check_daily_brief_coverage(
                _Conn(), issue_id="issue-1", target_day=date(2026, 9, 24), **kwargs,
            )


def test_reply_discovery_finds_old_threads_and_keeps_historical_hints_partial(monkeypatch):
    monkeypatch.setattr(coverage, "DailyBriefsRepository", _BriefRepo)
    calls = []
    queued = []

    def browse_page(*, page, forum_id, order):
        calls.append((order, page))
        if order == "dateline":
            return {"total_pages": 100, "items": [{"tid": 99, "posted_at": "2026-09-24"}]}
        # Default reply listing does not expose a total-page count.
        return {"total_pages": 0, "items": [
            {"tid": 99, "posted_at": "2026-09-24"},
            {"tid": 100, "posted_at": "2023-01-01", "last_reply_at": "2026-09-24T12:00:00"},
            {"tid": 101, "posted_at": "2023-01-01", "last_reply_at": "2026-09-25T12:00:00"},
        ]}

    def enqueue(**kwargs):
        queued.append(kwargs["tid"])
        return {"status": "queued"}

    monkeypatch.setattr(coverage, "_thread_floor_evidence", lambda *args: {})
    result = coverage.check_daily_brief_coverage(
        _Conn(), issue_id="issue-1", target_day=date(2026, 9, 24), forum_ids=[5],
        page_budget=4, archive_budget=2, browse_page=browse_page, enqueue_archive=enqueue,
    )
    assert calls == [("dateline", 1), ("dateline", 2), ("default", 1), ("default", 2)]
    assert queued == [99, 100]
    assert result["pages_used"] == 4
    assert result["candidate_count"] == 3
    assert result["candidates"][1]["candidate_reasons"] == ["thread_replied"]
    assert result["candidates"][2]["candidate_reasons"] == ["possible_target_day_activity"]
    assert result["candidates"][2]["archive"]["status"] == "not_queued_budget_exhausted"
    assert result["scanned_forum_ids"] == [5]
    assert result["complete"] is False
