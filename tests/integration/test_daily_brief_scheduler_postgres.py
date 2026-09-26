from __future__ import annotations

import os
import uuid
from datetime import datetime, time, timezone

import pytest
from sqlalchemy import text

from yamibo_mcp.application.daily_brief_scheduler import schedule_due_daily_briefs
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.daily_rules import DailyRulesRepository


def test_daily_rule_revision_schedule_atomicity_and_concurrent_claim(pg_engine):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    setup = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        migrate(setup, schema="public")
    finally:
        setup.close()

    suffix = uuid.uuid4().hex[:12]
    now = datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
    rule_id = f"daily-rule-{suffix}"
    db = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        with db:
            repo = DailyRulesRepository(db)
            paused = repo.create_rule(
                rule_id=f"paused-{suffix}", owner_id="scheduler-test-owner", forum_ids=[888003],
                timezone_name="Asia/Shanghai", execution_time="09:00",
                preparation_deadline="09:30", budget={}, now=now, enabled=False,
            )
            assert paused["next_run_at"] is None and paused["enabled"] is False
            with pytest.raises(ValueError, match="after execution_time"):
                repo.create_rule(
                    rule_id=f"invalid-{suffix}", owner_id="scheduler-test-owner", forum_ids=[888004],
                    timezone_name="Asia/Shanghai", execution_time="08:30",
                    preparation_deadline="08:00", budget={}, now=now,
                )
            rule = repo.create_rule(
                rule_id=rule_id, owner_id="scheduler-test-owner", forum_ids=[888001, 888002],
                timezone_name="Asia/Shanghai", execution_time="09:00",
                preparation_deadline="09:30", budget={"pages": 4, "seconds": 30}, now=now,
            )
            assert rule["revision"] == 1
            assert rule["next_run_at"] == datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)
            updated = repo.update_rule(
                rule_id=rule_id, owner_id="scheduler-test-owner", expected_revision=1,
                forum_ids=[888002], timezone_name="Asia/Shanghai", execution_time="09:00",
                preparation_deadline="09:30", budget={"page_budget": 17}, enabled=False, now=now,
            )
            assert updated["revision"] == 2 and updated["enabled"] is False
            assert updated["next_run_at"] is None
            with pytest.raises(ValueError, match="after execution_time"):
                repo.update_rule(
                    rule_id=rule_id, owner_id="scheduler-test-owner", expected_revision=2,
                    forum_ids=[888002], timezone_name="Asia/Shanghai", execution_time="09:00",
                    preparation_deadline="09:00", budget={}, enabled=True, now=now,
                )
            with pytest.raises(ValueError, match="stale"):
                repo.update_rule(
                    rule_id=rule_id, owner_id="scheduler-test-owner", expected_revision=1,
                    forum_ids=[888002], timezone_name="Asia/Shanghai", execution_time="09:00",
                    preparation_deadline="09:30", budget={}, enabled=True, now=now,
                )
            resumed = repo.update_rule(
                rule_id=rule_id, owner_id="scheduler-test-owner", expected_revision=2,
                forum_ids=[888002], timezone_name="Asia/Shanghai", execution_time="09:00",
                preparation_deadline="09:30", budget={"top_n": 6,"source_pid_limit": 40,
                    "statement_timeout_ms": 5000,"page_budget": 17,"archive_budget": 8,"archive_mode":"full"},
                enabled=True, now=now,
            )
            assert resumed["next_run_at"] == datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)

        # Set the due cursor explicitly so tests do not depend on wall-clock time.
        with pg_engine.begin() as raw:
            raw.execute(text("UPDATE daily_rules SET next_run_at=:due WHERE rule_id=:rule_id"),
                        {"due": datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc), "rule_id": rule_id})

        first = DatabaseConnection(pg_engine.connect(), backend="postgres")
        first_tx = first.begin()
        scheduled = schedule_due_daily_briefs(first, now=now)
        assert len(scheduled) == 1 and scheduled[0]["created"] is True

        # A concurrent poller skips the locked rule instead of duplicating it.
        second = DatabaseConnection(pg_engine.connect(), backend="postgres")
        try:
            with second:
                assert schedule_due_daily_briefs(second, now=now) == []
        finally:
            second.close()
        first_tx.commit()
        first.close()

        with pg_engine.connect() as raw:
            linked = raw.execute(text("""SELECT i.issue_id,i.target_day,i.queued_job_id,i.config_snapshot_json,
                j.job_type,j.payload_json,j.max_retries
                FROM daily_issues i JOIN jobs j ON j.job_id=i.queued_job_id
                WHERE i.rule_id=:rule_id"""), {"rule_id": rule_id}).mappings().one()
            assert str(linked["target_day"]) == "2026-09-24"
            assert linked["job_type"] == "daily_brief_report"
            assert linked["payload_json"]["issue_id"] == linked["issue_id"]
            assert linked["max_retries"] == 100
            assert linked["config_snapshot_json"]["forum_ids"] == [888002]
            assert linked["config_snapshot_json"]["top_n"] == 6
            assert linked["config_snapshot_json"]["source_pid_limit"] == 40
            assert linked["config_snapshot_json"]["statement_timeout_ms"] == 5000
            assert linked["config_snapshot_json"]["page_budget"] == 17
            assert linked["config_snapshot_json"]["archive_budget"] == 8
            assert linked["config_snapshot_json"]["archive_mode"] == "full"
            assert linked["config_snapshot_json"]["preparation_deadline"] == "2026-09-25T01:30:00+00:00"

        # Drive successive scheduled days with an injected clock and assert one
        # immutable logical period is queued for each date.
        for day in (26, 27):
            tick = datetime(2026, 9, day, 1, 0, tzinfo=timezone.utc)
            daily = DatabaseConnection(pg_engine.connect(), backend="postgres")
            try:
                with daily:
                    results = schedule_due_daily_briefs(daily, now=tick)
                    assert len(results) == 1 and results[0]["created"] is True
                    assert str(results[0]["target_day"]) == f"2026-09-{day - 1:02d}"
            finally:
                daily.close()
        with pg_engine.connect() as raw:
            assert raw.execute(text("SELECT count(*) FROM daily_issues WHERE rule_id=:rule_id"),
                               {"rule_id": rule_id}).scalar_one() == 3

        # Failure after the Job write rolls issue, Job, event, and cursor back together.
        with pg_engine.begin() as raw:
            raw.execute(text("DELETE FROM daily_issues WHERE rule_id=:rule_id"), {"rule_id": rule_id})
            raw.execute(text("UPDATE daily_rules SET next_run_at=:due WHERE rule_id=:rule_id"),
                        {"due": datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc), "rule_id": rule_id})
        failing = DatabaseConnection(pg_engine.connect(), backend="postgres")
        failing_tx = failing.begin()
        with pytest.raises(RuntimeError, match="injected"):
            schedule_due_daily_briefs(
                failing, now=now, after_job_insert=lambda: (_ for _ in ()).throw(RuntimeError("injected failure")),
            )
        failing_tx.rollback()
        failing.close()
        with pg_engine.connect() as raw:
            state = raw.execute(text("""SELECT r.next_run_at,
                (SELECT count(*) FROM daily_issues i WHERE i.rule_id=r.rule_id) AS issue_count
                FROM daily_rules r WHERE r.rule_id=:rule_id"""), {"rule_id": rule_id}).mappings().one()
            assert state["next_run_at"] == datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc)
            assert state["issue_count"] == 0
    finally:
        db.close()
