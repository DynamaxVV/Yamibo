from __future__ import annotations

import logging
import json
import time
from datetime import datetime, timezone
from typing import Any

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.domain.enums import JobStatus, JobType

LOG = logging.getLogger(__name__)

_STATE_KEY = "image_backfill_auto_scheduler"


def maybe_enqueue_image_backfill_dry_run(repo: JobsRepository, settings: Settings) -> bool:
    if not settings.image_backfill_enabled:
        return False
    dry_run = settings.image_backfill_dry_run

    state_repo = SystemStateRepository(repo.conn)
    state = state_repo.get_json(_STATE_KEY) or {}
    if not _within_budget(state, settings):
        return False

    candidate = _select_candidate(repo, settings, dry_run=dry_run)
    if candidate is None:
        _save_state(state_repo, state, enqueued=False, reason="no_candidate")
        return False

    payload = {
        "tid": int(candidate["tid"]),
        "forum_id": settings.image_backfill_forum_id,
        "dry_run": dry_run,
        "mode": "missing_only",
        "scope": "non_first_floor",
        "max_pages": settings.image_backfill_max_pages,
        "fixed_after": settings.image_backfill_fixed_after,
        "internal_auto": True,
        "candidate_reason": candidate["reason"],
        "sync_time": _serialize_sync_time(candidate.get("sync_time")),
    }
    repo.create(JobType.IMAGE_BACKFILL.value, tid=int(candidate["tid"]), payload=payload, max_retries=3)
    _save_state(state_repo, state, enqueued=True, reason="created")
    LOG.info(
        "Enqueued automatic image_backfill %s job tid=%s reason=%s",
        "dry-run" if dry_run else "apply",
        candidate["tid"],
        candidate["reason"],
    )
    return True


def _within_budget(state: dict[str, Any], settings: Settings) -> bool:
    now = time.time()
    interval = settings.image_backfill_auto_interval_seconds
    last_enqueued_at = float(state.get("last_enqueued_at") or 0.0)
    if interval > 0 and now - last_enqueued_at < interval:
        return False

    daily_limit = settings.image_backfill_daily_limit
    if daily_limit <= 0:
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    day = str(state.get("day") or "")
    count = int(state.get("count") or 0) if day == today else 0
    return count < daily_limit


def _save_state(
    state_repo: SystemStateRepository,
    state: dict[str, Any],
    *,
    enqueued: bool,
    reason: str,
) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    existing_day = str(state.get("day") or "")
    count = int(state.get("count") or 0) if existing_day == today else 0
    if enqueued:
        count += 1
        state["last_enqueued_at"] = time.time()
    state.update({"day": today, "count": count, "last_reason": reason})
    state_repo.set_json(_STATE_KEY, state)


def _select_candidate(repo: JobsRepository, settings: Settings, *, dry_run: bool) -> dict[str, Any] | None:
    forum_id = settings.image_backfill_forum_id
    random_func = "random()" if getattr(repo.conn, "backend", "sqlite") in {"postgres", "postgresql"} else "RANDOM()"
    rows = repo.conn.execute(
        f"""
        WITH asset_counts AS (
          SELECT tid, COUNT(*) FILTER (WHERE asset_type = 'image') AS image_assets
          FROM assets
          GROUP BY tid
        ), non_first AS (
          SELECT f.tid, COUNT(*) AS floors_without_assets
          FROM floors f
          JOIN threads t ON t.tid = f.tid
          LEFT JOIN assets a ON a.tid = f.tid AND a.pid = f.pid AND a.asset_type = 'image'
          WHERE t.forum_id = ?
            AND t.archive_status IN ('complete', 'partial')
            AND f.floor_no > 1
            AND f.has_images = ?
            AND a.asset_id IS NULL
          GROUP BY f.tid
        ), missing_assets AS (
          SELECT a.tid, COUNT(*) AS missing_asset_rows
          FROM assets a
          JOIN threads t ON t.tid = a.tid
          WHERE t.forum_id = ?
            AND t.archive_status IN ('complete', 'partial')
            AND a.asset_type = 'image'
            AND (a.local_path IS NULL OR a.local_path = '' OR COALESCE(a.status, '') IN ('missing', 'pending'))
          GROUP BY a.tid
        ), candidates AS (
          SELECT
            t.tid,
            t.sync_time,
            CASE
              WHEN COALESCE(nf.floors_without_assets, 0) > 0 THEN 'non_first_floor_has_images_without_asset'
              WHEN t.image_count > COALESCE(ac.image_assets, 0) THEN 'image_count_asset_gap'
              ELSE 'missing_or_pending_asset'
            END AS reason
          FROM threads t
          LEFT JOIN asset_counts ac ON ac.tid = t.tid
          LEFT JOIN non_first nf ON nf.tid = t.tid
          LEFT JOIN missing_assets ma ON ma.tid = t.tid
          WHERE t.forum_id = ?
            AND t.archive_status IN ('complete', 'partial')
            AND (
              COALESCE(nf.floors_without_assets, 0) > 0
              OR t.image_count > COALESCE(ac.image_assets, 0)
              OR COALESCE(ma.missing_asset_rows, 0) > 0
            )
        )
        SELECT tid, sync_time, reason
        FROM candidates
        ORDER BY {random_func}
        LIMIT 50
        """,
        (
            forum_id,
            True,
            forum_id,
            forum_id,
        ),
    ).fetchall()
    for row in rows:
        tid = int(row["tid"])
        if not _has_blocking_backfill_job(repo, tid=tid, dry_run=dry_run):
            return {"tid": row["tid"], "sync_time": row["sync_time"], "reason": row["reason"]}
    return None


def _has_blocking_backfill_job(repo: JobsRepository, *, tid: int, dry_run: bool) -> bool:
    rows = repo.conn.execute(
        """
        SELECT payload_json
        FROM jobs
        WHERE job_type = ?
          AND tid = ?
          AND status IN (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            JobType.IMAGE_BACKFILL.value,
            tid,
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
            JobStatus.PAUSED.value,
            JobStatus.SUCCEEDED.value,
            JobStatus.PARTIAL.value,
            JobStatus.FAILED.value,
            JobStatus.RETRYING.value,
            JobStatus.INTERRUPTED.value,
            JobStatus.CANCEL_REQUESTED.value,
        ),
    ).fetchall()
    if dry_run:
        return bool(rows)
    for row in rows:
        payload = _loads_payload(row["payload_json"])
        if not bool(payload.get("dry_run", True)):
            return True
    return False


def _loads_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in {None, ""}:
        return {}
    return json.loads(value)


def _serialize_sync_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
