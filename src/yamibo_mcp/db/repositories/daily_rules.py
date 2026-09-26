from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yamibo_mcp.db.transaction_scope import BorrowedConnection


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _as_time(value: time | str, field: str) -> time:
    if isinstance(value, time):
        if value.tzinfo is not None:
            raise ValueError(f"{field} must be a local wall-clock time")
        return value.replace(second=0, microsecond=0)
    try:
        return time.fromisoformat(value).replace(second=0, microsecond=0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an HH:MM local time") from exc


def _validate_daily_times(execution_time: time, preparation_deadline: time) -> None:
    if preparation_deadline <= execution_time:
        raise ValueError("preparation_deadline must be after execution_time on the same local day")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def _forum_ids(values: list[int]) -> list[int]:
    if not isinstance(values, list) or not values or any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("forum_ids must be a non-empty list of positive integers")
    if len(set(values)) != len(values):
        raise ValueError("forum_ids must not contain duplicates")
    return sorted(values)


def _budget(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("budget must be an object")
    bounded = {
        "top_n": (1, 100),
        "source_pid_limit": (1, 100),
        "statement_timeout_ms": (1, 30_000),
        "page_budget": (1, 200),
        "archive_budget": (1, 100),
    }
    normalized = dict(value)
    for key, (minimum, maximum) in bounded.items():
        if key in normalized and (type(normalized[key]) is not int or not minimum <= normalized[key] <= maximum):
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
    archive_mode = normalized.get("archive_mode", "text_only")
    if not isinstance(archive_mode, str) or archive_mode not in {"text_only", "full"}:
        raise ValueError("archive_mode must be text_only or full")
    return normalized


def _valid_local_datetime(day: date, wall_time: time, zone: ZoneInfo) -> datetime:
    """Resolve DST folds to fold=0 and gaps to the first valid local minute."""
    naive = datetime.combine(day, wall_time)
    for minute in range(181):
        candidate = naive + timedelta(minutes=minute)
        local = candidate.replace(tzinfo=zone, fold=0)
        if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == candidate:
            return local
    raise ValueError("could not resolve local time around timezone transition")


def next_run_after(now: datetime, zone_name: str, wall_time: time | str) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    zone = _zone(zone_name)
    run_time = _as_time(wall_time, "execution_time")
    local_now = now.astimezone(zone)
    for offset in range(3):
        candidate = _valid_local_datetime(local_now.date() + timedelta(days=offset), run_time, zone)
        if candidate.astimezone(timezone.utc) > now.astimezone(timezone.utc):
            return candidate.astimezone(timezone.utc)
    raise ValueError("could not find next execution time")


def latest_due_run(cursor: datetime, now: datetime, zone_name: str, wall_time: time | str) -> datetime:
    """Return the latest scheduled occurrence between cursor and now (inclusive)."""
    zone = _zone(zone_name)
    run_time = _as_time(wall_time, "execution_time")
    local_now = now.astimezone(zone)
    for offset in range(2):
        day = local_now.date() - timedelta(days=offset)
        candidate = _valid_local_datetime(day, run_time, zone).astimezone(timezone.utc)
        if cursor <= candidate <= now.astimezone(timezone.utc):
            return candidate
    return cursor.astimezone(timezone.utc)


class DailyRulesRepository:
    """PostgreSQL persistence for owner-scoped daily brief schedules."""

    def __init__(self, conn):
        if getattr(conn, "backend", "postgres") not in {"postgres", "postgresql"}:
            raise ValueError("daily rules require PostgreSQL")
        self.conn = BorrowedConnection(conn)

    def create_rule(
        self, *, owner_id: str, forum_ids: list[int], timezone_name: str,
        execution_time: time | str, preparation_deadline: time | str,
        budget: dict[str, Any], now: datetime, enabled: bool = True,
        rule_id: str | None = None,
    ) -> dict[str, Any]:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        normalized_forums = _forum_ids(forum_ids)
        normalized_budget = _budget(budget)
        _zone(timezone_name)
        run_at = _as_time(execution_time, "execution_time")
        deadline = _as_time(preparation_deadline, "preparation_deadline")
        _validate_daily_times(run_at, deadline)
        next_at = next_run_after(now, timezone_name, run_at) if enabled else None
        rule_id = rule_id or str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO daily_rules
               (rule_id,owner_id,forum_ids,timezone,execution_time,preparation_deadline,budget_json,
                enabled,revision,next_run_at)
               VALUES (:rule_id,:owner_id,CAST(:forum_ids AS JSONB),:timezone,:execution_time,:deadline,
                CAST(:budget AS JSONB),:enabled,1,:next_run_at)""",
            {"rule_id": rule_id, "owner_id": owner_id, "forum_ids": _json(normalized_forums),
             "timezone": timezone_name, "execution_time": run_at, "deadline": deadline,
             "budget": _json(normalized_budget), "enabled": enabled, "next_run_at": next_at},
        )
        return self.get_rule(rule_id, owner_id=owner_id)  # type: ignore[return-value]

    def update_rule(
        self, *, rule_id: str, owner_id: str, expected_revision: int,
        forum_ids: list[int], timezone_name: str, execution_time: time | str,
        preparation_deadline: time | str, budget: dict[str, Any], enabled: bool,
        now: datetime,
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if not owner_id.strip():
            raise ValueError("invalid daily rule fields")
        normalized_forums = _forum_ids(forum_ids)
        normalized_budget = _budget(budget)
        _zone(timezone_name)
        run_at = _as_time(execution_time, "execution_time")
        deadline = _as_time(preparation_deadline, "preparation_deadline")
        _validate_daily_times(run_at, deadline)
        next_at = next_run_after(now, timezone_name, run_at) if enabled else None
        result = self.conn.execute(
            """UPDATE daily_rules SET forum_ids=CAST(:forum_ids AS JSONB),timezone=:timezone,
                 execution_time=:execution_time,preparation_deadline=:deadline,budget_json=CAST(:budget AS JSONB),
                 enabled=:enabled,revision=revision+1,next_run_at=:next_run_at,updated_at=:now
               WHERE rule_id=:rule_id AND owner_id=:owner_id AND revision=:revision""",
            {"forum_ids": _json(normalized_forums), "timezone": timezone_name, "execution_time": run_at,
             "deadline": deadline, "budget": _json(normalized_budget), "enabled": enabled,
             "next_run_at": next_at, "now": now.astimezone(timezone.utc),
             "rule_id": rule_id, "owner_id": owner_id, "revision": expected_revision},
        )
        if result.rowcount != 1:
            raise ValueError("daily rule is missing, not owned by caller, or revision is stale")
        return self.get_rule(rule_id, owner_id=owner_id)  # type: ignore[return-value]

    def get_rule(self, rule_id: str, *, owner_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM daily_rules WHERE rule_id=:rule_id AND owner_id=:owner_id",
                                {"rule_id": rule_id, "owner_id": owner_id}).fetchone()
        if row is None:
            return None
        result = dict(row.items())
        result["budget_json"] = _decode(result["budget_json"])
        return result

    def list_rules(self, *, owner_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM daily_rules WHERE owner_id=:owner_id ORDER BY created_at,rule_id",
                                 {"owner_id": owner_id}).fetchall()
        result = []
        for row in rows:
            item = dict(row.items())
            item["budget_json"] = _decode(item["budget_json"])
            result.append(item)
        return result

    def lock_due_rules(self, *, now: datetime, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM daily_rules WHERE enabled=TRUE AND next_run_at<=:now
               ORDER BY next_run_at,rule_id LIMIT :limit FOR UPDATE SKIP LOCKED""",
            {"now": now.astimezone(timezone.utc), "limit": limit},
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row.items())
            item["budget_json"] = _decode(item["budget_json"])
            result.append(item)
        return result

    def advance_cursor(self, *, rule_id: str, next_at: datetime, now: datetime) -> None:
        result = self.conn.execute(
            "UPDATE daily_rules SET next_run_at=:next_at,updated_at=:now WHERE rule_id=:rule_id",
            {"next_at": next_at, "now": now.astimezone(timezone.utc), "rule_id": rule_id},
        )
        if result.rowcount != 1:
            raise ValueError("daily rule disappeared while being scheduled")
