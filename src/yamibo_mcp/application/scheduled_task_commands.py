"""Project-owned schedule operations; callers cannot choose actions or credentials."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from yamibo_mcp.application.scheduled_task_scheduler import trigger_task
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.scheduled_tasks import ScheduledTasksRepository

OWNER_ID = "local"


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("timestamp must be an ISO 8601 string with timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _identity(task_id: str, expected_revision: int | None = None) -> None:
    UUID(task_id)
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        raise ValueError("expected_revision must be a positive integer")


def list_scheduled_tasks() -> dict:
    with connect() as conn:
        return {"items": ScheduledTasksRepository(conn).list(owner_id=OWNER_ID)}


def read_scheduled_task(*, task_id: str) -> dict:
    _identity(task_id)
    with connect() as conn:
        repo = ScheduledTasksRepository(conn)
        task = repo.get(task_id, owner_id=OWNER_ID)
        if task is None or task.get("archived"):
            raise ValueError("scheduled task not found")
        return {"task": task, "occurrences": repo.occurrences(task_id, owner_id=OWNER_ID)}


def create_archive_schedule(*, name: str, tid: int, schedule_kind: str,
                            timezone_name: str = "Asia/Shanghai", cron: str | None = None,
                            at: str | None = None, mode: str = "text_only", forum_id: int | None = None) -> dict:
    if not isinstance(timezone_name, str) or not 1 <= len(timezone_name) <= 64:
        raise ValueError("timezone_name must contain 1–64 characters")
    if cron is not None and (not isinstance(cron, str) or len(cron) > 120):
        raise ValueError("cron must contain at most 120 characters")
    arguments = {"tid": tid, "mode": mode}
    if forum_id is not None:
        arguments["forum_id"] = forum_id
    with connect() as conn:
        return ScheduledTasksRepository(conn).create(
            owner_id=OWNER_ID, name=name, action="archive_thread", arguments=arguments,
            schedule_kind=schedule_kind, timezone_name=timezone_name, cron=cron,
            at=_timestamp(at) if at is not None else None, enabled=True, now=datetime.now(timezone.utc))


def set_scheduled_task_enabled(*, task_id: str, expected_revision: int, enabled: bool) -> dict:
    _identity(task_id, expected_revision)
    if type(enabled) is not bool:
        raise ValueError("enabled must be boolean")
    with connect() as conn:
        repo = ScheduledTasksRepository(conn)
        task = repo.get(task_id, owner_id=OWNER_ID)
        if task is None or task.get("archived"):
            raise ValueError("scheduled task not found")
        return repo.update(task_id=task_id, expected_revision=expected_revision, owner_id=OWNER_ID,
                           name=task["name"], action=task["action"], arguments=task["arguments"],
                           schedule_kind=task["schedule_kind"], timezone_name=task["timezone"],
                           cron=task["cron"], at=task["at"], enabled=enabled, now=datetime.now(timezone.utc))


def trigger_scheduled_task(*, task_id: str, expected_revision: int, requested_at: str) -> dict:
    _identity(task_id, expected_revision)
    stamp = _timestamp(requested_at)
    with connect() as conn:
        ScheduledTasksRepository(conn)
        task = conn.execute("SELECT revision FROM scheduled_tasks WHERE task_id=:id AND owner_id=:owner AND NOT archived FOR UPDATE",
                            {"id": task_id, "owner": OWNER_ID}).fetchone()
        if task is None or task["revision"] != expected_revision:
            raise ValueError("REVISION_CONFLICT")
        return trigger_task(conn, task_id=task_id, owner_id=OWNER_ID, now=stamp)
