from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from yamibo_mcp.application.scheduled_task_actions import validate_action
from yamibo_mcp.db.transaction_scope import BorrowedConnection


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("unknown timezone") from exc


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def cron_occurrence(expression: str, zone_name: str, now: datetime, *, previous=False) -> datetime:
    """Local cron clock: skip nonexistent minutes, use fold=0 once on DST fallback."""
    now = utc(now)
    zone = _zone(zone_name)
    if len(expression.split()) != 5 or not croniter.is_valid(expression):
        raise ValueError("cron must be a valid five-field expression")
    if len(croniter.expand(expression)[0][0]) != 1 or croniter.expand(expression)[0][0] == ["*"]:
        raise ValueError("scheduled cron must specify one minute per hour (minimum interval one hour)")
    local = now.astimezone(zone).replace(tzinfo=None)
    iterator = croniter(expression, local + (timedelta(microseconds=1) if previous else timedelta()), max_years_between_matches=8)
    for _ in range(200):
        naive = iterator.get_prev(datetime) if previous else iterator.get_next(datetime)
        candidate = naive.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) != naive:
            continue
        if (previous and candidate <= now) or (not previous and candidate > now):
            return candidate
    raise ValueError("could not resolve cron occurrence")


def _row(row):
    if row is None:
        return None
    item = dict(row.items())
    for key in ("arguments", "result"):
        if isinstance(item.get(key), str):
            item[key] = json.loads(item[key])
    return item


class ScheduledTasksRepository:
    def __init__(self, conn):
        if getattr(conn, "backend", None) not in {"postgres", "postgresql"}:
            raise ValueError("scheduled tasks require PostgreSQL")
        self.conn = BorrowedConnection(conn)

    def _values(self, *, owner_id, name, action, arguments, schedule_kind, timezone_name, now, cron=None, at=None, enabled=True):
        now = utc(now)
        if not isinstance(owner_id, str) or not owner_id.strip() or not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("owner_id and name (1–120 characters) are required")
        _zone(timezone_name)
        args = validate_action(action, arguments)
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if schedule_kind == "cron" and at is None and isinstance(cron, str):
            next_at = cron_occurrence(cron, timezone_name, now)
        elif schedule_kind == "at" and cron is None:
            at = utc(at)
            if enabled and at <= now:
                raise ValueError("one-shot at must be in the future")
            next_at = at
        else:
            raise ValueError("choose cron with expression or at with timestamp")
        return dict(owner_id=owner_id, name=name.strip(), action=action, arguments=json.dumps(args), schedule_kind=schedule_kind,
                    timezone=timezone_name, now=now, cron=cron, at=at, enabled=enabled, next_run_at=next_at if enabled else None)

    def create(self, **kwargs):
        values = self._values(**kwargs)
        values["task_id"] = str(uuid.uuid4())
        self.conn.execute("""INSERT INTO scheduled_tasks
            (task_id,owner_id,name,action,arguments,schedule_kind,timezone,cron,at,enabled,revision,next_run_at,created_at,updated_at)
            VALUES (:task_id,:owner_id,:name,:action,CAST(:arguments AS JSONB),:schedule_kind,:timezone,:cron,:at,:enabled,1,:next_run_at,:now,:now)""", values)
        return self.get(values["task_id"], owner_id=values["owner_id"])

    def update(self, *, task_id, expected_revision, **kwargs):
        values = self._values(**kwargs)
        values.update(task_id=task_id, expected_revision=expected_revision)
        result = self.conn.execute("""UPDATE scheduled_tasks SET name=:name,action=:action,arguments=CAST(:arguments AS JSONB),
            schedule_kind=:schedule_kind,timezone=:timezone,cron=:cron,at=:at,enabled=:enabled,next_run_at=:next_run_at,
            revision=revision+1,updated_at=:now WHERE task_id=:task_id AND owner_id=:owner_id AND revision=:expected_revision AND NOT archived""", values)
        if result.rowcount != 1:
            raise ValueError("REVISION_CONFLICT")
        return self.get(task_id, owner_id=values["owner_id"])

    def get(self, task_id, *, owner_id):
        return _row(self.conn.execute("SELECT * FROM scheduled_tasks WHERE task_id=:task_id AND owner_id=:owner_id", dict(task_id=task_id, owner_id=owner_id)).fetchone())

    def list(self, *, owner_id):
        return [_row(row) for row in self.conn.execute("SELECT * FROM scheduled_tasks WHERE owner_id=:owner_id AND NOT archived ORDER BY created_at,task_id", dict(owner_id=owner_id)).fetchall()]

    def delete(self, task_id, *, owner_id, expected_revision):
        return self.conn.execute("UPDATE scheduled_tasks SET archived=TRUE,enabled=FALSE,next_run_at=NULL,revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE task_id=:task_id AND owner_id=:owner_id AND revision=:revision AND NOT archived", dict(task_id=task_id, owner_id=owner_id, revision=expected_revision)).rowcount == 1

    def occurrences(self, task_id, *, owner_id):
        return [_row(row) for row in self.conn.execute("""SELECT o.* FROM scheduled_task_occurrences o JOIN scheduled_tasks t USING(task_id)
            WHERE t.task_id=:task_id AND t.owner_id=:owner_id ORDER BY scheduled_at DESC LIMIT 50""", dict(task_id=task_id, owner_id=owner_id)).fetchall()]
