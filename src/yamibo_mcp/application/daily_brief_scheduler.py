from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from yamibo_mcp.db.repositories.daily_rules import (
    DailyRulesRepository,
    _valid_local_datetime,
    latest_due_run,
    next_run_after,
)
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.transaction_scope import BorrowedConnection


_CATCH_UP_WINDOW = timedelta(hours=48)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def schedule_due_daily_briefs(
    connection,
    *,
    now: datetime,
    limit: int = 20,
    after_job_insert: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Atomically queue at most one current logical issue per due rule.

    The caller owns the transaction and must commit or roll it back as a whole.
    This allows the daemon to group scheduling with its surrounding transaction
    while ensuring a failed job/issue insert never advances a rule cursor.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if limit < 1:
        return []
    conn = BorrowedConnection(connection)
    repo = DailyRulesRepository(conn)
    now_utc = now.astimezone(timezone.utc)
    scheduled: list[dict[str, Any]] = []
    for rule in repo.lock_due_rules(now=now_utc, limit=limit):
        run_time = rule["execution_time"]
        cursor = rule["next_run_at"].astimezone(timezone.utc)
        latest_run = latest_due_run(cursor, now_utc, rule["timezone"], run_time)
        next_at = next_run_after(now_utc, rule["timezone"], run_time)
        repo.advance_cursor(rule_id=rule["rule_id"], next_at=next_at, now=now_utc)

        # Catch-up issues are created only for the latest missed run and only
        # while it remains within the explicit 48-hour recovery window.
        if now_utc - latest_run > _CATCH_UP_WINDOW:
            continue

        zone = ZoneInfo(rule["timezone"])
        run_day = latest_run.astimezone(zone).date()
        target_day = run_day - timedelta(days=1)
        day_start = _valid_local_datetime(target_day, time.min, zone)
        day_end = _valid_local_datetime(target_day + timedelta(days=1), time.min, zone)
        deadline_time = rule["preparation_deadline"]
        deadline_at = _valid_local_datetime(run_day, deadline_time, zone)
        deadline_utc = deadline_at.astimezone(timezone.utc)
        if deadline_utc <= latest_run:
            raise ValueError("preparation deadline must occur after the scheduled execution")
        issue_id = str(uuid.uuid4())
        snapshot = {
            "rule_id": rule["rule_id"],
            "rule_revision": int(rule["revision"]),
            "forum_ids": rule["forum_ids"],
            "timezone": rule["timezone"],
            "execution_time": run_time.isoformat(timespec="minutes"),
            "preparation_deadline": deadline_utc.isoformat(),
            "budget": rule["budget_json"],
            "top_n": rule["budget_json"].get("top_n", 10),
            "source_pid_limit": rule["budget_json"].get("source_pid_limit", 30),
            "statement_timeout_ms": rule["budget_json"].get("statement_timeout_ms", 3000),
            "page_budget": rule["budget_json"].get("page_budget", 200),
            "archive_budget": rule["budget_json"].get("archive_budget", 100),
            "archive_mode": rule["budget_json"].get("archive_mode", "text_only"),
        }
        created = conn.execute(
            """INSERT INTO daily_issues
               (issue_id,source_kind,rule_id,rule_revision,owner_id,target_day,timezone,
                window_start,window_end,config_snapshot_json,state)
               VALUES (:issue_id,'scheduled',:rule_id,:revision,:owner_id,:target_day,:timezone,
                :window_start,:window_end,CAST(:snapshot AS JSONB),'created')
               ON CONFLICT DO NOTHING RETURNING issue_id""",
            {"issue_id": issue_id, "rule_id": rule["rule_id"], "revision": rule["revision"],
             "owner_id": rule["owner_id"], "target_day": target_day, "timezone": rule["timezone"],
             "window_start": day_start.astimezone(timezone.utc), "window_end": day_end.astimezone(timezone.utc),
             "snapshot": _json(snapshot)},
        ).fetchone()
        if created is None:
            # An existing issue for (rule, day) is the durable idempotency key.
            scheduled.append({"rule_id": rule["rule_id"], "target_day": target_day, "created": False})
            continue

        payload = {"issue_id": issue_id, "owner_id": rule["owner_id"], "session_id": None}
        from yamibo_mcp.application.daily_brief_service import DAILY_JOB_MAX_RETRIES, JOB_TYPE

        job = JobsRepository(BorrowedConnection(connection)).create(
            JOB_TYPE, payload=payload, max_retries=DAILY_JOB_MAX_RETRIES, resumable=True,
        )
        job_id = job.job_id
        # Keep the injected scheduler clock authoritative despite the shared Job
        # repository's standard wall-clock timestamps.
        conn.execute("UPDATE jobs SET created_at=:now,updated_at=:now WHERE job_id=:job_id",
                     {"now": now_utc, "job_id": job_id})
        conn.execute("UPDATE job_events SET created_at=:now WHERE job_id=:job_id AND event_type='job.created'",
                     {"now": now_utc, "job_id": job_id})
        if after_job_insert is not None:
            after_job_insert()
        linked = conn.execute(
            "UPDATE daily_issues SET queued_job_id=:job_id,state='queued' WHERE issue_id=:issue_id",
            {"job_id": job_id, "issue_id": issue_id},
        )
        if linked.rowcount != 1:
            raise RuntimeError("daily issue disappeared before Job association")
        scheduled.append({"rule_id": rule["rule_id"], "issue_id": issue_id,
                          "job_id": job_id, "target_day": target_day, "created": True})
    return scheduled
