from __future__ import annotations

import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from yamibo_mcp.application import daily_brief_service as service
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.daily_briefs import DailyBriefsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.errors import LeaseNotAcquired


def test_manual_issue_job_creation_is_idempotent_and_retry_appends_job(pg_engine, monkeypatch):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    setup = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        migrate(setup, schema="public")
    finally:
        setup.close()

    suffix = uuid.uuid4().hex[:12]
    session_id = f"daily-brief-session-{suffix}"
    with pg_engine.begin() as raw:
        raw.execute(
            text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"),
            {"id": session_id, "data": json.dumps({"id": session_id, "owner_id": "daily-owner"})},
        )

    monkeypatch.setattr(service, "load_settings", lambda: object())
    monkeypatch.setattr(
        service,
        "connect",
        lambda *_args, **_kwargs: DatabaseConnection(pg_engine.connect(), backend="postgres"),
    )

    created = service.create_manual_issue(
        session_id=session_id, target_day=date(2026, 9, 24), forum_ids=[55],
    )
    repeated = service.create_manual_issue(
        session_id=session_id, target_day=date(2026, 9, 24), forum_ids=[55],
    )
    assert created["created"] is True and created["queued"] is True
    assert repeated["created"] is False and repeated["job_id"] == created["job_id"]
    assert repeated["issue"]["queued_job_id"] == created["job_id"]
    assert service.list_issues(session_id=session_id)["items"][0]["issue"]["issue_id"] == created["issue"]["issue_id"]
    assert service.get_issue(issue_id=created["issue"]["issue_id"], session_id=session_id)["queued_job"]["job_id"] == created["job_id"]

    other_session = f"daily-brief-other-{suffix}"
    with pg_engine.begin() as raw:
        raw.execute(text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"),
                    {"id": other_session, "data": json.dumps({"owner_id": "other-owner"})})
    with pytest.raises(service.DailyBriefError) as denied:
        service.get_issue(issue_id=created["issue"]["issue_id"], session_id=other_session)
    assert denied.value.status_code == 404

    with pg_engine.begin() as raw:
        linked = raw.execute(
            text("SELECT job_type,payload_json,max_retries FROM jobs WHERE job_id=:id"),
            {"id": created["job_id"]},
        ).mappings().one()
        assert linked["job_type"] == "daily_brief_report"
        payload = linked["payload_json"] if isinstance(linked["payload_json"], dict) else json.loads(linked["payload_json"])
        assert payload["issue_id"] == created["issue"]["issue_id"]
        assert linked["max_retries"] == service.DAILY_JOB_MAX_RETRIES
        raw.execute(text("UPDATE jobs SET status='partial' WHERE job_id=:id"), {"id": created["job_id"]})

    retried = service.retry_manual_issue(
        issue_id=created["issue"]["issue_id"], session_id=session_id,
        regeneration_reason="补充昨日未完成资料",
    )
    assert retried["reused"] is False and retried["job_id"] != created["job_id"]
    assert retried["issue"]["queued_job_id"] == retried["job_id"]

    with pg_engine.begin() as raw:
        issue_count = raw.execute(
            text("SELECT COUNT(*) FROM daily_issues WHERE owner_id='daily-owner' AND target_day='2026-09-24'")
        ).scalar_one()
        job_count = raw.execute(
            text("SELECT COUNT(*) FROM jobs WHERE job_type='daily_brief_report' AND payload_json->>'issue_id'=:issue"),
            {"issue": created["issue"]["issue_id"]},
        ).scalar_one()
        assert issue_count == 1
        assert job_count == 2


def test_pending_archive_reuses_attempt_then_terminal_revision_is_idempotent(pg_engine, monkeypatch):
    session_id, issue_id, service_conn, report_job, archive_job, scan_calls, facts_calls = _service_fixture(
        pg_engine, monkeypatch, "pending"
    )
    monkeypatch.setattr(service, "_read_candidate_sources", lambda *_args: [{"receipt_id": "read-1", "source": "verified_floor_read"}])
    try:
        repo = _ResumableRepo(service_conn)
        first = service.execute_daily_brief_job(
            repo, report_job, object(), coverage_checker=_coverage_factory(archive_job, scan_calls),
            facts_builder=_facts_factory(facts_calls), editor_factory=_BrokenEditor,
        )
        assert first["status"] == "waiting_for_archive"
        assert scan_calls["count"] == 1 and facts_calls["count"] == 0
        attempt_rows = DailyBriefsRepository(service_conn).list_attempts(issue_id)
        assert len(attempt_rows) == 1 and attempt_rows[0]["job_id"] == report_job.job_id
        assert repo.retries == 0  # deferral belongs to the daemon handler, not the resumable service pass

        with pg_engine.begin() as raw:
            raw.execute(text("UPDATE jobs SET status='succeeded',artifacts_json=:artifacts WHERE job_id=:id"), {
                "id": archive_job,
                "artifacts": json.dumps({"pages_fetched": 1, "total_pages_detected": 1,
                                         "fetch_stopped_reason": "last_page", "archive_status": "complete"}),
            })
        second = service.execute_daily_brief_job(
            repo, report_job, object(), coverage_checker=_coverage_factory(archive_job, scan_calls),
            facts_builder=_facts_factory(facts_calls), editor_factory=_BrokenEditor,
        )
        again = service.execute_daily_brief_job(
            repo, report_job, object(), coverage_checker=_coverage_factory(archive_job, scan_calls),
            facts_builder=_facts_factory(facts_calls), editor_factory=_BrokenEditor,
        )
        assert second["status"] == "partial" and second["revision_id"] == again["revision_id"]
        assert again["reused_revision"] is True
        assert scan_calls["count"] == 1 and facts_calls["count"] == 1
        attempts = DailyBriefsRepository(service_conn).list_attempts(issue_id)
        revisions = DailyBriefsRepository(service_conn).list_report_revisions(issue_id)
        assert len(attempts) == 1 and len(revisions) == 1
        assert revisions[0]["status"] == "partial"
        assert "今日事实保留" in revisions[0]["report_json"]["facts"]["facts_marker"]
        assert revisions[0]["source_receipts_json"][0]["source"] == "verified_floor_read"
        assert any("模型编辑未完成" in reason for reason in revisions[0]["gap_reasons_json"])
        resolved = DailyBriefsRepository(service_conn).get_report_revision(revisions[0]["revision_id"])
        assert resolved["owner_id"] == "owner-pending"
        assert resolved["issue_id"] == issue_id and resolved["source_receipts_json"][0]["receipt_id"] == "read-1"
    finally:
        service_conn.close()


def test_archive_wait_deadline_produces_partial_revision(pg_engine, monkeypatch):
    session_id, issue_id, service_conn, report_job, archive_job, scan_calls, facts_calls = _service_fixture(
        pg_engine, monkeypatch, "deadline"
    )
    monkeypatch.setattr(service, "_read_candidate_sources", lambda *_args: [{"receipt_id": "read-1", "source": "verified_floor_read"}])
    try:
        repo = _ResumableRepo(service_conn)
        first = service.execute_daily_brief_job(
            repo, report_job, object(), coverage_checker=_coverage_factory(archive_job, scan_calls),
            facts_builder=_facts_factory(facts_calls), editor_factory=_BrokenEditor,
        )
        assert first["status"] == "waiting_for_archive"
        attempt = DailyBriefsRepository(service_conn).list_attempts(issue_id)[0]
        expired = {**attempt["coverage_json"], "coverage_deadline_at": "2020-01-01T00:00:00+00:00"}
        service_conn.execute("UPDATE daily_attempts SET coverage_json=CAST(:data AS JSONB) WHERE attempt_id=:id",
                             {"data": json.dumps(expired), "id": attempt["attempt_id"]})
        result = service.execute_daily_brief_job(
            repo, report_job, object(), coverage_checker=_coverage_factory(archive_job, scan_calls),
            facts_builder=_facts_factory(facts_calls), editor_factory=_BrokenEditor,
        )
        assert result["status"] == "partial"
        assert result["revision_id"]
        revision = DailyBriefsRepository(service_conn).list_report_revisions(issue_id)[0]
        assert archive_job in revision["report_json"]["facts"]["coverage"]["pending_candidate_job_ids_at_deadline"]
        assert "partial" == revision["status"]
        assert scan_calls["count"] == 1 and facts_calls["count"] == 1
    finally:
        service_conn.close()


def test_scheduled_preparation_deadline_is_used_across_resumes(pg_engine, monkeypatch):
    _session_id, issue_id, conn, job, archive_job, scan_calls, facts_calls = _service_fixture(
        pg_engine, monkeypatch, "scheduled-deadline"
    )
    monkeypatch.setattr(service, "_read_candidate_sources", lambda *_args: [])
    deadline = "2026-09-24T08:30:00+08:00"
    try:
        conn.execute(
            "UPDATE daily_issues SET source_kind='scheduled', manual_issue_key=NULL, rule_id='deadline-rule', rule_revision=1, config_snapshot_json=CAST(:config AS JSONB) WHERE issue_id=:id",
            {"config": json.dumps({"forum_ids": [55], "preparation_deadline": deadline}), "id": issue_id},
        )
        repo = _ResumableRepo(conn)
        execute = lambda now: service.execute_daily_brief_job(
            repo, job, object(), coverage_checker=_coverage_factory(archive_job, scan_calls),
            facts_builder=_facts_factory(facts_calls), editor_factory=_BrokenEditor, clock=lambda: now,
        )
        before = execute(datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc))
        assert before["status"] == "waiting_for_archive"
        attempt = DailyBriefsRepository(conn).list_attempts(issue_id)[0]
        assert attempt["coverage_json"]["coverage_deadline_at"] == "2026-09-24T00:30:00+00:00"

        after = execute(datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc))
        assert after["status"] == "partial"
        revision = DailyBriefsRepository(conn).list_report_revisions(issue_id)[0]
        assert archive_job in revision["report_json"]["facts"]["coverage"]["pending_candidate_job_ids_at_deadline"]
        assert scan_calls["count"] == 1 and facts_calls["count"] == 1
    finally:
        conn.close()


def test_daily_brief_lease_keeper_renews_and_fences_stale_worker(pg_engine, monkeypatch):
    _session_id, _issue_id, conn, job, _archive_job, _scan_calls, _facts_calls = _service_fixture(
        pg_engine, monkeypatch, "lease"
    )
    try:
        owner_repo = JobsRepository(conn, owner_id="daily-lease-worker")
        conn.execute("UPDATE jobs SET status='queued', worker_id=NULL, lease_until=NULL WHERE job_id=:id", {"id": job.job_id})
        conn.commit()
        owner_repo.acquire(job.job_id, "daily-lease-worker", 2)
        settings = SimpleNamespace(worker_heartbeat_seconds=1)
        keeper = service._JobLeaseKeeper(owner_repo, job.job_id, "daily-lease-worker", 2, settings)
        keeper.start()
        time.sleep(2.2)
        keeper.check()
        row = conn.execute("SELECT lease_until, heartbeat_at FROM jobs WHERE job_id=:id", {"id": job.job_id}).fetchone()
        assert service._datetime_utc(row["lease_until"]) > datetime.now(timezone.utc)
        renewed_at = service._datetime_utc(row["heartbeat_at"])
        assert renewed_at > datetime.now(timezone.utc) - timedelta(seconds=2)
        keeper.stop()

        conn.execute("UPDATE jobs SET status='retrying', lease_until='2000-01-01T00:00:00+00:00' WHERE job_id=:id", {"id": job.job_id})
        conn.commit()
        JobsRepository(conn, owner_id="new-worker").acquire(job.job_id, "new-worker", 30)
        with pytest.raises(LeaseNotAcquired):
            keeper._beat_once()
    finally:
        conn.close()


def test_real_daily_brief_handler_defers_waiting_job(pg_engine, monkeypatch):
    _session_id, _issue_id, conn, job, _archive_job, _scan_calls, _facts_calls = _service_fixture(
        pg_engine, monkeypatch, "handler-wait"
    )
    worker_id = "daily-handler-wait"
    repo = _leased_handler_repo(conn, job.job_id, worker_id)
    monkeypatch.setattr(service, "execute_daily_brief_job", lambda *_args, **_kwargs: {
        "status": "waiting_for_archive", "candidate_job_ids": ["archive-pending"],
        "coverage_deadline_at": "2026-09-25T00:00:00+00:00",
    })
    try:
        service.handle_daily_brief_report(repo, job, worker_id, 10, SimpleNamespace(worker_heartbeat_seconds=2))
        assert repo.get(job.job_id).status == "retrying"
        assert repo.get(job.job_id).retry_count == 1
    finally:
        conn.close()


def test_real_daily_brief_handler_marks_completed_report_partial(pg_engine, monkeypatch):
    _session_id, _issue_id, conn, job, _archive_job, _scan_calls, _facts_calls = _service_fixture(
        pg_engine, monkeypatch, "handler-complete"
    )
    worker_id = "daily-handler-complete"
    repo = _leased_handler_repo(conn, job.job_id, worker_id)
    monkeypatch.setattr(service, "execute_daily_brief_job", lambda *_args, **_kwargs: {
        "status": "partial", "revision_id": str(uuid.uuid4()), "attempt_id": str(uuid.uuid4()),
    })
    try:
        service.handle_daily_brief_report(repo, job, worker_id, 10, SimpleNamespace(worker_heartbeat_seconds=2))
        assert repo.get(job.job_id).status == "partial"
    finally:
        conn.close()


def test_real_daily_brief_handler_cannot_finalize_after_lease_transfer(pg_engine, monkeypatch):
    _session_id, _issue_id, conn, job, _archive_job, _scan_calls, _facts_calls = _service_fixture(
        pg_engine, monkeypatch, "handler-stale"
    )
    worker_id = "daily-handler-stale"
    repo = _leased_handler_repo(conn, job.job_id, worker_id)

    def steal_lease(*_args, **_kwargs):
        with pg_engine.begin() as raw:
            raw.execute(text("UPDATE jobs SET status='retrying', lease_until='2000-01-01T00:00:00+00:00' WHERE job_id=:id"),
                        {"id": job.job_id})
        JobsRepository(conn, owner_id="new-daily-worker").acquire(job.job_id, "new-daily-worker", 30)
        return {"status": "partial", "revision_id": str(uuid.uuid4())}

    monkeypatch.setattr(service, "execute_daily_brief_job", steal_lease)
    try:
        with pytest.raises(LeaseNotAcquired):
            service.handle_daily_brief_report(repo, job, worker_id, 10, SimpleNamespace(worker_heartbeat_seconds=2))
        state = repo.get(job.job_id)
        assert state.status == "running" and state.worker_id == "new-daily-worker"
    finally:
        conn.close()


def _leased_handler_repo(conn, job_id, worker_id):
    conn.execute("UPDATE jobs SET status='queued', worker_id=NULL, lease_until=NULL WHERE job_id=:id", {"id": job_id})
    conn.commit()
    repo = JobsRepository(conn, owner_id=worker_id)
    repo.acquire(job_id, worker_id, 10)
    return repo


def test_manual_issue_job_insert_failure_rolls_back_issue(pg_engine, monkeypatch):
    setup = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        migrate(setup, schema="public")
    finally:
        setup.close()
    session_id = f"daily-brief-rollback-{uuid.uuid4().hex[:10]}"
    with pg_engine.begin() as raw:
        raw.execute(text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"),
                    {"id": session_id, "data": json.dumps({"owner_id": "rollback-owner"})})
    monkeypatch.setattr(service, "load_settings", lambda: object())
    monkeypatch.setattr(service, "connect", lambda *_a, **_k: DatabaseConnection(pg_engine.connect(), backend="postgres"))

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("simulated insert fault")

    monkeypatch.setattr(service, "_enqueue_issue_job", fail_enqueue)
    with pytest.raises(RuntimeError, match="simulated"):
        service.create_manual_issue(session_id=session_id, target_day=date(2026, 9, 24), forum_ids=[56])
    with pg_engine.connect() as raw:
        assert raw.execute(text("SELECT COUNT(*) FROM daily_issues WHERE owner_id='rollback-owner'")).scalar_one() == 0


class _ResumableRepo:
    def __init__(self, conn):
        self.conn = conn
        self.retries = 0

    def retry_later(self, *_args, **_kwargs):
        self.retries += 1
        return True


class _BrokenEditor:
    def __init__(self, _settings):
        pass

    async def edit(self, facts, receipts):
        return {"status": "partial", "facts": facts, "editorial": {"recommendations": []},
                "editor_error": "simulated model failure", "gaps": ["模型编辑失败"]}


def _service_fixture(pg_engine, monkeypatch, suffix):
    setup = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        migrate(setup, schema="public")
    finally:
        setup.close()
    session_id = f"daily-brief-{suffix}-{uuid.uuid4().hex[:8]}"
    with pg_engine.begin() as raw:
        raw.execute(text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"),
                    {"id": session_id, "data": json.dumps({"owner_id": f"owner-{suffix}"})})
    monkeypatch.setattr(service, "load_settings", lambda: object())
    monkeypatch.setattr(service, "connect", lambda *_a, **_k: DatabaseConnection(pg_engine.connect(), backend="postgres"))
    created = service.create_manual_issue(session_id=session_id, target_day=date(2026, 9, 24), forum_ids=[55])
    issue_id = created["issue"]["issue_id"]
    archive_job = f"archive_{uuid.uuid4().hex[:16]}"
    with pg_engine.begin() as raw:
        raw.execute(text("INSERT INTO jobs(job_id,job_type,payload_json,status,max_retries,resumable) VALUES(:id,'sync_thread','{}','queued',3,TRUE)"),
                    {"id": archive_job})
        raw.execute(text("UPDATE jobs SET status='running' WHERE job_id=:id"), {"id": created["job_id"]})
    conn = DatabaseConnection(pg_engine.connect(), backend="postgres")
    job = SimpleNamespace(job_id=created["job_id"], payload={"issue_id": issue_id, "owner_id": f"owner-{suffix}"})
    return session_id, issue_id, conn, job, archive_job, {"count": 0}, {"count": 0}


def _coverage_factory(archive_job, calls):
    def check(conn, *, issue_id, target_day, forum_ids, timezone_name, **_kwargs):
        calls["count"] += 1
        attempt = DailyBriefsRepository(conn).start_attempt(issue_id=issue_id)
        receipt = {
            "coverage_status": "partial", "complete": False, "full_forum_coverage_proven": False,
            "target_day": target_day.isoformat(), "timezone": timezone_name,
            "target_day_start_utc": "2026-09-23T16:00:00+00:00",
            "target_day_end_utc": "2026-09-24T16:00:00+00:00",
            "candidate_count": 1, "candidates": [{"tid": 1234, "forum_id": forum_ids[0],
                "archive": {"job_id": archive_job, "job_status": "queued", "job_terminal": False,
                            "status": "pending_or_invalid"}}], "gap_reasons": ["coverage is partial"],
        }
        DailyBriefsRepository(conn).finish_attempt(attempt_id=attempt["attempt_id"], status="partial", coverage=receipt)
        return {"attempt_id": attempt["attempt_id"], **receipt}
    return check


def _facts_factory(calls):
    def build(conn, *, target_day, timezone_name, **_kwargs):
        calls["count"] += 1
        return {
            "target_day": target_day.isoformat(), "timezone": timezone_name,
            "window_start_utc": "2026-09-23T16:00:00+00:00", "window_end_utc": "2026-09-24T16:00:00+00:00",
            "coverage": {"coverage_status": "local_snapshot_only", "gap_reasons": ["local only"]},
            "facts_marker": "今日事实保留", "candidates": [],
        }
    return build
