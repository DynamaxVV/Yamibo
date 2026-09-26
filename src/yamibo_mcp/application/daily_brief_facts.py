"""Deterministic daily discussion facts and their auditable statistics receipt."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from yamibo_mcp.db.repositories.daily_briefs import DailyBriefsRepository


FACTS_VERSION = "daily-brief-facts-v2"
DEFAULT_TOP_N = 10
MAX_TOP_N = 100
DEFAULT_SOURCE_PID_LIMIT = 30
MAX_SOURCE_PID_LIMIT = 100
DEFAULT_STATEMENT_TIMEOUT_MS = 3000
MAX_STATEMENT_TIMEOUT_MS = 30000
LOCAL_COVERAGE_GAP = "只检查了当前本地归档快照，未扫描或证明全论坛目标日覆盖。"


_FACT_QUERY = """
WITH ranked AS (
  SELECT t.tid, t.forum_id,
         COALESCE(t.display_title, t.raw_title) AS title,
         t.pub_time AS thread_created_at,
         COUNT(DISTINCT f.pid) AS pid_count,
         COUNT(DISTINCT COALESCE(NULLIF(f.publisher_uid, ''), NULLIF(f.publisher, ''))) AS participant_count,
         MIN(f.pub_time) AS first_floor_at,
         MAX(f.pub_time) AS last_floor_at
  FROM forums forum
  JOIN threads t ON t.forum_id = forum.forum_id
  JOIN floors f ON f.tid = t.tid
  WHERE ((forum.content_kind = 'discussion' AND t.content_kind = 'discussion')
         OR (forum.forum_id = 30 AND forum.content_kind = 'comic' AND t.content_kind = 'comic'))
    AND forum.enabled IS TRUE
    AND f.pid IS NOT NULL
    AND f.pub_time >= :window_start
    AND f.pub_time < :window_end
  GROUP BY t.tid, t.forum_id, t.display_title, t.raw_title, t.pub_time
  ORDER BY COUNT(DISTINCT f.pid) DESC, t.tid ASC
  LIMIT :scan_limit
)
SELECT ranked.*, sampled.source_pids, sampled.source_times
FROM ranked
CROSS JOIN LATERAL (
  SELECT array_agg(sample.pid ORDER BY sample.rn) AS source_pids,
         array_agg(sample.pub_time ORDER BY sample.rn) AS source_times
  FROM (
    SELECT f.pid, f.pub_time,
           row_number() OVER (ORDER BY f.pub_time ASC, f.pid ASC) AS rn
    FROM floors f
    WHERE f.tid = ranked.tid AND f.pid IS NOT NULL
      AND f.pub_time >= :window_start AND f.pub_time < :window_end
  ) sample
  WHERE sample.rn IN (
    SELECT CASE WHEN LEAST(:source_pid_limit, ranked.pid_count) = 1 THEN 1
           ELSE 1 + round(i * (ranked.pid_count - 1)::numeric /
                          (LEAST(:source_pid_limit, ranked.pid_count) - 1))
           END
    FROM generate_series(0, LEAST(:source_pid_limit, ranked.pid_count)::integer - 1) i
  )
) sampled
ORDER BY ranked.pid_count DESC, ranked.tid ASC
"""


def target_day_window(target_day: date, timezone_name: str = "Asia/Shanghai") -> tuple[datetime, datetime]:
    """Return the target local calendar day as an aware UTC half-open interval."""
    if not isinstance(target_day, date) or isinstance(target_day, datetime):
        raise ValueError("target_day must be a date")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone_name}") from exc
    start_local = datetime.combine(target_day, time.min, tzinfo=zone)
    end_local = datetime.combine(target_day + timedelta(days=1), time.min, tzinfo=zone)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def make_manual_issue_key(
    *, owner_id: str, target_day: date, timezone_name: str, config_snapshot: dict[str, Any]
) -> str:
    """Build a stable idempotency key for same-owner, same-config manual issues."""
    if not owner_id.strip():
        raise ValueError("owner_id is required")
    canonical = {
        "owner_id": owner_id,
        "target_day": target_day.isoformat(),
        "timezone": timezone_name,
        "config": config_snapshot,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_manual_daily_issue(
    conn,
    *,
    owner_id: str,
    target_day: date,
    forum_ids: list[int],
    timezone_name: str = "Asia/Shanghai",
    top_n: int = DEFAULT_TOP_N,
    source_pid_limit: int = DEFAULT_SOURCE_PID_LIMIT,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> tuple[dict[str, Any], bool]:
    _validate_limits(top_n, source_pid_limit, statement_timeout_ms)
    normalized_forums = sorted({int(forum_id) for forum_id in forum_ids if int(forum_id) > 0})
    if not normalized_forums:
        raise ValueError("at least one forum_id is required")
    start, end = target_day_window(target_day, timezone_name)
    config = {
        "forum_ids": normalized_forums,
        "top_n": top_n,
        "source_pid_limit": source_pid_limit,
        "statement_timeout_ms": statement_timeout_ms,
        "facts_version": FACTS_VERSION,
    }
    key = make_manual_issue_key(
        owner_id=owner_id,
        target_day=target_day,
        timezone_name=timezone_name,
        config_snapshot=config,
    )
    issue, created = DailyBriefsRepository(conn).create_or_get_manual_issue(
        manual_issue_key=key,
        owner_id=owner_id,
        target_day=target_day,
        timezone=timezone_name,
        window_start=start,
        window_end=end,
        config_snapshot=config,
    )
    return issue, created


def build_daily_brief_facts(
    conn,
    *,
    target_day: date,
    timezone_name: str = "Asia/Shanghai",
    forum_ids: list[int] | None = None,
    top_n: int = DEFAULT_TOP_N,
    source_pid_limit: int = DEFAULT_SOURCE_PID_LIMIT,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Aggregate only local target-day discussion floors; never infer remote coverage."""
    if getattr(conn, "backend", "postgres") not in {"postgres", "postgresql"}:
        raise ValueError("daily brief facts require PostgreSQL")
    _validate_limits(top_n, source_pid_limit, statement_timeout_ms)
    start, end = target_day_window(target_day, timezone_name)
    requested_forums = sorted({int(value) for value in (forum_ids or []) if int(value) > 0})
    enabled_forum_rows = conn.execute(
        """SELECT forum_id FROM forums
           WHERE (content_kind = 'discussion' OR (forum_id = 30 AND content_kind = 'comic')) AND enabled IS TRUE
           ORDER BY forum_id"""
    ).fetchall()
    enabled_forums = [int(row["forum_id"]) for row in enabled_forum_rows]
    included_forums = [forum_id for forum_id in enabled_forums if not requested_forums or forum_id in requested_forums]
    if requested_forums and set(requested_forums) != set(included_forums):
        # Non-enabled or non-discussion IDs are omitted and surfaced in the receipt.
        excluded_forums = sorted(set(requested_forums) - set(included_forums))
    else:
        excluded_forums = []

    # The local statement timeout bounds database work; no local index can prove full forum coverage.
    previous_timeout = conn.execute("SELECT current_setting('statement_timeout') AS timeout").fetchone()["timeout"]
    conn.execute(
        "SELECT set_config('statement_timeout', :timeout, true)",
        {"timeout": f"{statement_timeout_ms}ms"},
    )
    parameters = {
        "window_start": start,
        "window_end": end,
        "scan_limit": top_n + 1,
        "source_pid_limit": source_pid_limit,
    }
    forum_filter = ""
    if requested_forums:
        forum_filter = " AND t.forum_id = ANY(:requested_forums)"
        parameters["requested_forums"] = requested_forums
    query = _FACT_QUERY.replace("  GROUP BY", f"{forum_filter}\n  GROUP BY")
    rows = conn.execute(query, parameters).fetchall()
    conn.execute("SELECT set_config('statement_timeout', :timeout, true)", {"timeout": previous_timeout})
    truncated = len(rows) > top_n
    rows = rows[:top_n]
    candidates: list[dict[str, Any]] = []
    total_target_day_pids = 0
    for row in rows:
        pid_count = int(row["pid_count"])
        total_target_day_pids += pid_count
        pids = list(row["source_pids"] or [])
        times = list(row["source_times"] or [])
        source_receipt = [
            {
                "pid": int(pid),
                "tid": int(row["tid"]),
                "published_at": _iso(times[index]),
                "target_day": target_day.isoformat(),
                "facts_version": FACTS_VERSION,
            }
            for index, pid in enumerate(pids)
        ]
        candidates.append(
            {
                "tid": int(row["tid"]),
                "forum_id": int(row["forum_id"]),
                "title": row["title"],
                "thread_created_at": _iso(row["thread_created_at"]),
                "activity_kind": _activity_kind(row["thread_created_at"], start, end),
                "target_day_pid_count": pid_count,
                "participant_count": int(row["participant_count"]),
                "first_floor_at": _iso(row["first_floor_at"]),
                "last_floor_at": _iso(row["last_floor_at"]),
                "source_receipts": source_receipt,
                "source_receipt_truncated": len(source_receipt) < pid_count,
            }
        )

    return {
        "target_day": target_day.isoformat(),
        "timezone": timezone_name,
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "facts_version": FACTS_VERSION,
        "methodology": {
            "activity": "按 floors.pub_time 落在目标日半开 UTC 窗内的唯一非空 PID 计数；按 TID 汇总。",
            "thread_filter": "启用的讨论板块及其 discussion 帖；另含漫画区（30）的 comic 帖；严格限定请求板块。",
            "thread_creation_date_is_ignored": True,
            "activity_classification": "主题创建时间仅用于新旧分类，不参与活跃候选过滤；时间缺失、无时区或晚于窗口记为未知。",
            "post_day_corrections_after_window_excluded": True,
            "ordering": "target_day_pid_count DESC, tid ASC",
            "receipt_pid_limit_per_candidate": source_pid_limit,
            "receipt_sampling": "按当日时间顺序等距抽取楼层，额度至少为 2 时包含首尾；不是随机或观点代表性抽样。",
            "statement_timeout_ms": statement_timeout_ms,
        },
        "coverage": {
            "coverage_status": "local_snapshot_only",
            "local_archived_forum_ids": included_forums,
            "requested_forum_ids": requested_forums or None,
            "excluded_forum_ids": excluded_forums,
            "gap_reasons": [LOCAL_COVERAGE_GAP],
            "full_forum_coverage_proven": False,
            "complete": False,
        },
        "counts": {
            "candidate_count_returned": len(candidates),
            "new_thread_count_returned": sum(c["activity_kind"] == "new_thread" for c in candidates),
            "old_thread_count_returned": sum(c["activity_kind"] == "old_thread" for c in candidates),
            "unknown_thread_count_returned": sum(c["activity_kind"] == "unknown" for c in candidates),
            "candidate_list_truncated": truncated,
            "candidate_limit": top_n,
            "target_day_pid_count_across_returned_candidates": total_target_day_pids,
        },
        "candidates": candidates,
    }


def _validate_limits(top_n: int, source_pid_limit: int, statement_timeout_ms: int) -> None:
    if not isinstance(top_n, int) or isinstance(top_n, bool) or not 1 <= top_n <= MAX_TOP_N:
        raise ValueError(f"top_n must be between 1 and {MAX_TOP_N}")
    if not isinstance(source_pid_limit, int) or isinstance(source_pid_limit, bool) or not 1 <= source_pid_limit <= MAX_SOURCE_PID_LIMIT:
        raise ValueError(f"source_pid_limit must be between 1 and {MAX_SOURCE_PID_LIMIT}")
    if not isinstance(statement_timeout_ms, int) or isinstance(statement_timeout_ms, bool) or not 100 <= statement_timeout_ms <= MAX_STATEMENT_TIMEOUT_MS:
        raise ValueError(f"statement_timeout_ms must be between 100 and {MAX_STATEMENT_TIMEOUT_MS}")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _activity_kind(value: Any, start: datetime, end: datetime) -> str:
    if value is None:
        return "unknown"
    try:
        created = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return "unknown"
    if created.tzinfo is None:
        return "unknown"
    if start <= created < end:
        return "new_thread"
    return "old_thread" if created < start else "unknown"
