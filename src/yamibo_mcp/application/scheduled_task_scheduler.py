"""Caller-owned transaction; schedule occurrence, job and cursor commit together."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from yamibo_mcp.application.scheduled_task_actions import execute_action
from yamibo_mcp.db.repositories.scheduled_tasks import ScheduledTasksRepository, _row, cron_occurrence, utc
from yamibo_mcp.db.transaction_scope import BorrowedConnection
from yamibo_mcp.errors import classify_error


def _dispatch(conn, task, due, now, *, manual=False, after_job_insert=None):
    inserted = conn.execute("""INSERT INTO scheduled_task_occurrences
        (task_id,scheduled_at,revision,status,result,created_at,manual)
        VALUES (:id,:due,:revision,'pending','{}'::jsonb,:now,:manual)
        ON CONFLICT DO NOTHING RETURNING task_id""",
        dict(id=task["task_id"], due=due, revision=task["revision"], now=now, manual=manual)).fetchone()
    if inserted is None:
        return {"task_id": task["task_id"], "created": False}
    if not manual and now - due > timedelta(hours=48):
        result, status = {"reason": "outside 48-hour catch-up window"}, "skipped"
    else:
        if manual:
            result = execute_action(conn, task["action"], task["arguments"])
            status = "queued" if result.get("job_id") else "completed"
        else:
            # A failing action must undo its own writes without starving the
            # remaining due tasks. Occurrence creation stays outside savepoint.
            conn.execute("SAVEPOINT scheduled_action")
            try:
                result = execute_action(conn, task["action"], task["arguments"])
            except Exception as exc:
                conn.execute("ROLLBACK TO SAVEPOINT scheduled_action")
                result = {"error_code": classify_error(exc)[:100],
                          "message": "Scheduled action could not be queued; its writes were rolled back."}
                status = "failed"
            else:
                status = "queued" if result.get("job_id") else "completed"
            finally:
                conn.execute("RELEASE SAVEPOINT scheduled_action")
        # Test/integration transaction hooks deliberately escape action recovery.
        if after_job_insert and status != "failed":
            after_job_insert()
    conn.execute("""UPDATE scheduled_task_occurrences SET status=:status,result=CAST(:result AS JSONB)
        WHERE task_id=:id AND scheduled_at=:due AND manual=:manual""", dict(status=status,result=json.dumps(result),id=task["task_id"],due=due,manual=manual))
    return dict(task_id=task["task_id"], scheduled_at=due, status=status, created=True, result=result)


def schedule_due_tasks(connection, *, now: datetime, limit: int = 20, after_job_insert=None):
    ScheduledTasksRepository(connection)
    conn = BorrowedConnection(connection)
    now = utc(now)
    if limit < 1:
        return []
    rows = conn.execute("""SELECT * FROM scheduled_tasks WHERE enabled AND NOT archived AND next_run_at<=:now
        ORDER BY next_run_at,task_id LIMIT :limit FOR UPDATE SKIP LOCKED""", dict(now=now,limit=limit)).fetchall()
    results = []
    for row in rows:
        task = _row(row)
        due = task["next_run_at"]
        next_at = None
        if task["schedule_kind"] == "cron":
            due = max(due, cron_occurrence(task["cron"], task["timezone"], now, previous=True))
            next_at = cron_occurrence(task["cron"], task["timezone"], now)
        results.append(_dispatch(conn, task, due, now, after_job_insert=after_job_insert))
        conn.execute("UPDATE scheduled_tasks SET next_run_at=:next,enabled=:enabled,updated_at=:now WHERE task_id=:id",
                     dict(next=next_at,enabled=next_at is not None,now=now,id=task["task_id"]))
    return results


def trigger_task(connection, *, task_id: str, owner_id: str, now: datetime, expected_revision: int | None = None):
    ScheduledTasksRepository(connection)
    conn = BorrowedConnection(connection)
    now = utc(now)
    task = _row(conn.execute("SELECT * FROM scheduled_tasks WHERE task_id=:id AND owner_id=:owner AND NOT archived FOR UPDATE", dict(id=task_id,owner=owner_id)).fetchone())
    if task is None:
        raise ValueError("task missing or not owned")
    if expected_revision is not None and task["revision"] != expected_revision:
        raise ValueError("REVISION_CONFLICT")
    # Manual timestamp is also a retry key; replaying the same request clock is idempotent.
    return _dispatch(conn, task, now, now, manual=True)
