from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.domain.job_state import new_job_id
from yamibo_mcp.storage.images import _is_valid_image_file
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.time_utils import utc_now_iso
from yamibo_mcp.yamibo.urls import is_yamibo_site_image_url

LOG = logging.getLogger(__name__)

_STATE_KEY = "image_backfill_auto_scheduler"
_CAMPAIGN = "metadata_reconcile_v1"
_MODE = "reconcile_missing"
_SCHEDULER_LOCK = threading.Lock()
_SCAN_BATCH_SIZE = 200
# A cancellation request is terminal from the perspective of idle scheduling:
# it is no longer acquirable and must not keep maintenance work waiting.
_FOREGROUND_WORK_STATUSES = (
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.INTERRUPTED.value,
    JobStatus.PAUSED.value,
)
# A stable signed bigint for pg_try_advisory_xact_lock().  The lock is scoped
# to the transaction, so a pooled PostgreSQL session cannot retain it.
_POSTGRES_ADVISORY_LOCK_KEY = 0x59414D49424F5F32
_BLOCKING_STATUSES = (
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.PAUSED.value,
    JobStatus.SUCCEEDED.value,
    JobStatus.PARTIAL.value,
    JobStatus.FAILED.value,
    JobStatus.RETRYING.value,
    JobStatus.INTERRUPTED.value,
    JobStatus.CANCEL_REQUESTED.value,
)


def maybe_enqueue_image_backfill_dry_run(repo: JobsRepository, settings: Settings) -> bool:
    """Run at most one bounded automatic image-backfill scan."""
    # This guard must remain before opening a transaction/query.  Operators use
    # the setting as the immediate production stop switch.
    if not settings.image_backfill_enabled:
        return False
    if not _SCHEDULER_LOCK.acquire(blocking=False):
        LOG.info("image_backfill scheduler skipped lock_acquired=false reason=local_lock_busy")
        return False

    try:
        # The daemon connection can have a read transaction left by the worker
        # loop. Clear it before creating the explicit scheduler transaction.
        repo.conn.commit()
        with repo.conn.begin():
            if not _try_postgres_advisory_lock(repo):
                LOG.info("image_backfill scheduler skipped lock_acquired=false reason=advisory_lock_busy")
                return False

            state_repo = SystemStateRepository(repo.conn)
            state = state_repo.get_json(_STATE_KEY) or {}
            if not _within_budget(state, settings):
                return False
            if _has_foreground_work(repo):
                _save_state(state_repo, state, enqueued=False, reason="foreground_work", cursor_tid=_state_cursor(state), wrap_count=int(state.get("scan_wrap_count") or 0))
                return False

            dry_run = settings.image_backfill_dry_run
            cursor_before = _state_cursor(state)
            scan = _select_candidate_batch(
                repo,
                settings,
                dry_run=dry_run,
                cursor_tid=cursor_before,
                campaign=_CAMPAIGN,
            )
            candidate = scan["candidate"]
            cursor_after = int(scan["cursor_after"])
            wrap_count = int(scan["wrap_count"])
            if candidate is None:
                _save_state(
                    state_repo,
                    state,
                    enqueued=False,
                    reason="scanned_no_candidate",
                    cursor_tid=cursor_after,
                    wrap_count=wrap_count,
                )
                _log_scan(scan, cursor_before=cursor_before, cursor_after=cursor_after, wrap_count=wrap_count)
                return False

            fingerprint = f"{int(candidate['tid'])}:{candidate['reason']}"
            if _has_live_automatic_job(repo, campaign=_CAMPAIGN, fingerprint=fingerprint):
                _save_state(state_repo, state, enqueued=False, reason="live_duplicate", cursor_tid=cursor_after, wrap_count=wrap_count)
                return False

            payload = {
                "tid": int(candidate["tid"]),
                "forum_id": settings.image_backfill_forum_id,
                "dry_run": dry_run,
                "mode": _MODE,
                "campaign": _CAMPAIGN,
                "candidate_fingerprint": fingerprint,
                "scope": "selected",
                "include_first_floor": True,
                "max_pages": settings.image_backfill_max_pages,
                "fixed_after": settings.image_backfill_fixed_after,
                "internal_auto": True,
                "candidate_reason": candidate["reason"],
                "sync_time": _serialize_sync_time(candidate.get("sync_time")),
            }
            job_id = _create_job_without_commit(
                repo,
                tid=int(candidate["tid"]),
                payload=payload,
                max_retries=3,
            )
            _save_state(
                state_repo,
                state,
                enqueued=True,
                reason="created",
                cursor_tid=cursor_after,
                wrap_count=wrap_count,
            )
            _log_scan(scan, cursor_before=cursor_before, cursor_after=cursor_after, wrap_count=wrap_count)
            LOG.info(
                "Enqueued automatic image_backfill %s job_id=%s tid=%s reason=%s",
                "dry-run" if dry_run else "apply",
                job_id,
                candidate["tid"],
                candidate["reason"],
            )
            return True
    except Exception:
        # The transaction context rolls back state/job writes. In particular,
        # a failed query must not advance the persistent cursor.
        LOG.exception("image_backfill scheduler scan failed")
        return False
    finally:
        _SCHEDULER_LOCK.release()


def _try_postgres_advisory_lock(repo: JobsRepository) -> bool:
    backend = str(getattr(repo.conn, "backend", "sqlite") or "sqlite").lower()
    if backend not in {"postgres", "postgresql"}:
        return True
    result = repo.conn.execute(
        "SELECT pg_try_advisory_xact_lock(?) AS acquired",
        (_POSTGRES_ADVISORY_LOCK_KEY,),
    )
    row = result.fetchone() if result is not None else None
    return bool(row and row["acquired"])


def _within_budget(state: dict[str, Any], settings: Settings) -> bool:
    now = time.time()
    interval = settings.image_backfill_auto_interval_seconds
    last_enqueued_at = float(state.get("last_enqueued_at") or 0.0)
    last_checked_at = float(state.get("last_checked_at") or 0.0)
    if interval > 0 and now - max(last_enqueued_at, last_checked_at) < interval:
        return False

    daily_limit = settings.image_backfill_daily_limit
    if daily_limit <= 0:
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    day = str(state.get("day") or "")
    count = int(state.get("count") or 0) if day == today else 0
    return count < daily_limit


def _state_cursor(state: dict[str, Any]) -> int:
    try:
        return max(int(state.get("scan_cursor_tid") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _save_state(
    state_repo: SystemStateRepository,
    state: dict[str, Any],
    *,
    enqueued: bool,
    reason: str,
    cursor_tid: int,
    wrap_count: int,
) -> None:
    """Persist scheduler JSON without committing the caller's transaction."""
    today = datetime.now(timezone.utc).date().isoformat()
    existing_day = str(state.get("day") or "")
    count = int(state.get("count") or 0) if existing_day == today else 0
    now = time.time()
    if enqueued:
        count += 1
        state["last_enqueued_at"] = now
    state.update(
        {
            "day": today,
            "count": count,
            "last_checked_at": now,
            "last_reason": reason,
            "scan_cursor_tid": max(int(cursor_tid), 0),
            "scan_wrap_count": max(int(wrap_count), 0),
        }
    )
    state_repo.conn.execute(
        """
        INSERT INTO system_state (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (_STATE_KEY, json.dumps(state, ensure_ascii=False), utc_now_iso()),
    )


def _select_candidate_batch(
    repo: JobsRepository,
    settings: Settings,
    *,
    dry_run: bool,
    cursor_tid: int,
    campaign: str | None = None,
) -> dict[str, Any]:
    """Scan one bounded TID batch and return its first unblocked candidate."""
    started = time.perf_counter()
    forum_id = settings.image_backfill_forum_id
    state = SystemStateRepository(repo.conn).get_json(_STATE_KEY) or {}
    previous_wrap_count = int(state.get("scan_wrap_count") or 0)
    batch_rows = repo.conn.execute(
        """
        SELECT t.tid
        FROM threads t
        WHERE t.forum_id = ?
          AND t.archive_status IN ('complete', 'partial')
          AND t.tid > ?
        ORDER BY t.tid ASC
        LIMIT ?
        """,
        (forum_id, max(int(cursor_tid), 0), _SCAN_BATCH_SIZE),
    ).fetchall()
    if batch_rows:
        batch_tids = [int(row["tid"]) for row in batch_rows]
        cursor_after = batch_tids[-1]
        wrap_count = previous_wrap_count
    else:
        batch_tids = []
        cursor_after = 0
        # A wrap is persisted without immediately scanning a second batch.
        wrap_count = previous_wrap_count + 1 if cursor_tid > 0 else previous_wrap_count

    rows = repo.conn.execute(
        """
        WITH batch AS (
          SELECT t.tid, t.sync_time, t.image_count, t.missing_images_json
          FROM threads t
          WHERE t.forum_id = ?
            AND t.archive_status IN ('complete', 'partial')
            AND t.tid > ?
          ORDER BY t.tid ASC
          LIMIT ?
        ), asset_counts AS (
          SELECT a.tid, COUNT(*) FILTER (WHERE a.asset_type IN ('image', 'attachment')) AS image_assets
          FROM assets a
          JOIN batch b ON b.tid = a.tid
          GROUP BY a.tid
        ), non_first AS (
          SELECT f.tid, COUNT(*) AS floors_without_assets
          FROM floors f
          JOIN batch b ON b.tid = f.tid
          LEFT JOIN assets a ON a.tid = f.tid AND a.pid = f.pid AND a.asset_type IN ('image', 'attachment')
          WHERE f.has_images = ?
            AND a.asset_id IS NULL
          GROUP BY f.tid
        ), missing_assets AS (
          SELECT a.tid, COUNT(*) AS missing_asset_rows
          FROM assets a
          JOIN batch b ON b.tid = a.tid
          WHERE a.asset_type IN ('image', 'attachment')
            AND (a.local_path IS NULL OR a.local_path = '' OR COALESCE(a.status, '') IN ('missing', 'pending'))
          GROUP BY a.tid
        ), candidates AS (
          SELECT
            b.tid,
            b.sync_time,
            CASE
              WHEN COALESCE(nf.floors_without_assets, 0) > 0 THEN 'non_first_floor_has_images_without_asset'
              WHEN b.image_count > COALESCE(ac.image_assets, 0) THEN 'image_count_asset_gap'
              WHEN COALESCE(CAST(b.missing_images_json AS TEXT), '') NOT IN ('', '[]', '{}') THEN 'metadata_missing_slot'
              ELSE 'missing_or_pending_asset'
            END AS reason
          FROM batch b
          LEFT JOIN asset_counts ac ON ac.tid = b.tid
          LEFT JOIN non_first nf ON nf.tid = b.tid
          LEFT JOIN missing_assets ma ON ma.tid = b.tid
          WHERE (
              COALESCE(nf.floors_without_assets, 0) > 0
              OR b.image_count > COALESCE(ac.image_assets, 0)
              OR COALESCE(ma.missing_asset_rows, 0) > 0
              OR COALESCE(CAST(b.missing_images_json AS TEXT), '') NOT IN ('', '[]', '{}')
          )
        )
        SELECT tid, sync_time, reason
        FROM candidates
        ORDER BY tid ASC
        LIMIT ?
        """,
        (forum_id, max(int(cursor_tid), 0), _SCAN_BATCH_SIZE, True, _SCAN_BATCH_SIZE),
    ).fetchall()

    eligible_rows = [
        row for row in rows
        if _has_auto_repairable_site_image(settings, int(row["tid"]))
    ]
    tids = [int(row["tid"]) for row in eligible_rows]
    blocked_tids = _blocking_backfill_tids(repo, tids, dry_run=dry_run, campaign=campaign)
    candidate = next(
        (
            {"tid": int(row["tid"]), "sync_time": row["sync_time"], "reason": row["reason"]}
            for row in eligible_rows
            if int(row["tid"]) not in blocked_tids
        ),
        None,
    )
    return {
        "candidate": candidate,
        "batch_size": len(batch_tids),
        "candidate_count": len(eligible_rows),
        "non_site_candidate_count": len(rows) - len(eligible_rows),
        "blocking_count": len(blocked_tids),
        "cursor_after": cursor_after,
        "wrap_count": wrap_count,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
    }


def _has_auto_repairable_site_image(settings: Settings, tid: int) -> bool:
    """Return whether a candidate has a missing first-party image target.

    The SQL candidate query intentionally detects broad archive gaps.  The
    metadata file is the only local source that retains the remote URL for
    floor rows without an asset, so use it to avoid scheduling external-only
    images.  Missing or unreadable metadata fails open to preserve recovery of
    older archives whose metadata predates image slots.
    """
    data_dir_value = getattr(settings, "data_dir", None)
    if data_dir_value is None:
        # Lightweight scheduler test doubles and legacy callers may not expose
        # the storage root; retain the previous candidate behavior there.
        return True

    paths = StoragePaths(Path(data_dir_value))
    metadata_path = paths.thread_metadata(tid)
    if not metadata_path.exists():
        return True
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return True
    if not isinstance(metadata, dict):
        return True

    observed_remote_urls = False
    floors = metadata.get("floors") or []
    if isinstance(floors, list):
        for floor in floors:
            if not isinstance(floor, dict):
                continue
            raw_urls = floor.get("remote_image_urls")
            if raw_urls is None:
                raw_urls = floor.get("image_urls") or []
            slots = floor.get("image_slots") or []
            if not isinstance(slots, list):
                slots = []
            if not raw_urls:
                raw_urls = [slot.get("remote_url") for slot in slots if isinstance(slot, dict)]
            if not isinstance(raw_urls, list):
                continue
            for index, raw_url in enumerate(raw_urls):
                url = str(raw_url or "").strip()
                if not _is_remote_image_url(url):
                    continue
                observed_remote_urls = True
                if not is_yamibo_site_image_url(url):
                    continue
                slot = slots[index] if index < len(slots) and isinstance(slots[index], dict) else {}
                if _metadata_slot_needs_repair(paths, tid, slot):
                    return True

    missing_urls = [
        *(metadata.get("missing_image_urls") or []),
        *(metadata.get("missing_shared_image_urls") or []),
    ]
    remote_missing_urls = [str(value).strip() for value in missing_urls if _is_remote_image_url(str(value).strip())]
    if remote_missing_urls:
        observed_remote_urls = True
        if any(is_yamibo_site_image_url(url) for url in remote_missing_urls):
            return True

    # No remote URL means this is likely an older metadata shape.  Keep the
    # SQL candidate eligible rather than silently losing a valid repair.
    return not observed_remote_urls


def _is_remote_image_url(url: str) -> bool:
    lowered = url.lower()
    return lowered.startswith(("http://", "https://"))


def _metadata_slot_needs_repair(paths: StoragePaths, tid: int, slot: dict[str, Any]) -> bool:
    status = str(slot.get("status") or "").strip().lower()
    if status == "skipped":
        return False
    local_path = str(slot.get("local_path") or "").strip()
    if not local_path or status in {"missing", "missing_shared", "pending"}:
        return True
    path = Path(local_path)
    if path.is_absolute():
        resolved = path
    elif local_path.startswith("shared/"):
        resolved = paths.data_dir / path
    else:
        resolved = paths.thread_dir(tid) / path
    return not _is_valid_image_file(resolved)


_LIVE_STATUSES = (
    JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.RETRYING.value,
    JobStatus.INTERRUPTED.value, JobStatus.CANCEL_REQUESTED.value, JobStatus.PAUSED.value,
)


def _blocking_backfill_tids(repo: JobsRepository, tids: list[int], *, dry_run: bool, campaign: str | None = None) -> set[int]:
    if not tids:
        return set()
    placeholders = ",".join("?" for _ in tids)
    rows = repo.conn.execute(
        f"""
        SELECT tid, payload_json
        FROM jobs
        WHERE job_type = ?
          AND tid IN ({placeholders})
          AND status IN ({','.join('?' for _ in (_LIVE_STATUSES if campaign else _BLOCKING_STATUSES))})
        """,
        (JobType.IMAGE_BACKFILL.value, *tids, *(_LIVE_STATUSES if campaign else _BLOCKING_STATUSES)),
    ).fetchall()
    blocked: set[int] = set()
    for row in rows:
        tid = int(row["tid"])
        payload = _loads_payload(row["payload_json"])
        if campaign and payload.get("campaign") != campaign:
            continue
        if dry_run or not bool(payload.get("dry_run", True)):
            blocked.add(tid)
    return blocked


def _has_live_automatic_job(repo: JobsRepository, *, campaign: str, fingerprint: str) -> bool:
    rows = repo.conn.execute(
        f"SELECT payload_json FROM jobs WHERE job_type = ? AND status IN ({','.join('?' for _ in _LIVE_STATUSES)})",
        (JobType.IMAGE_BACKFILL.value, *_LIVE_STATUSES),
    ).fetchall()
    for row in rows:
        payload = _loads_payload(row["payload_json"])
        # Automatic maintenance is globally single-flight.  The campaign and
        # fingerprint are still checked for diagnostics/dedup callers, but a
        # different candidate must not start alongside an existing one.
        if payload.get("internal_auto") is True:
            return True
    return False


def _has_foreground_work(repo: JobsRepository) -> bool:
    placeholders = ",".join("?" for _ in _FOREGROUND_WORK_STATUSES)
    result = repo.conn.execute(
        f"""
        SELECT job_type, payload_json FROM jobs
        WHERE status IN ({placeholders})
        """,
        _FOREGROUND_WORK_STATUSES,
    )
    rows = result.fetchall() if result is not None else []
    for row in rows:
        payload = _loads_payload(row["payload_json"])
        if row["job_type"] != JobType.IMAGE_BACKFILL.value or not payload.get("internal_auto"):
            return True
    return False


def _select_candidate(repo: JobsRepository, settings: Settings, *, dry_run: bool) -> dict[str, Any] | None:
    """Compatibility wrapper for callers/tests of the former helper."""
    return _select_candidate_batch(repo, settings, dry_run=dry_run, cursor_tid=0)["candidate"]


def _has_blocking_backfill_job(repo: JobsRepository, *, tid: int, dry_run: bool) -> bool:
    """Compatibility wrapper; new scans use one batch query instead."""
    return int(tid) in _blocking_backfill_tids(repo, [int(tid)], dry_run=dry_run)


def _create_job_without_commit(
    repo: JobsRepository,
    *,
    tid: int,
    payload: dict[str, Any],
    max_retries: int,
) -> str:
    """Insert an automatic job and its creation event in the active transaction."""
    job_id = new_job_id(JobType.IMAGE_BACKFILL.value)
    now = utc_now_iso()
    repo.conn.execute(
        """
        INSERT INTO jobs (
          job_id, parent_job_id, job_type, tid, payload_json, status, priority,
          max_retries, resumable, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            None,
            JobType.IMAGE_BACKFILL.value,
            tid,
            json.dumps(payload, ensure_ascii=False),
            JobStatus.QUEUED.value,
            100,
            max_retries,
            True,
            now,
            now,
        ),
    )
    repo.conn.execute(
        """
        INSERT INTO job_events (job_id, event_type, status, stage, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, "job.created", JobStatus.QUEUED.value, None, "{}", now),
    )
    return job_id


def _log_scan(scan: dict[str, Any], *, cursor_before: int, cursor_after: int, wrap_count: int) -> None:
    LOG.info(
        "image_backfill scan elapsed_ms=%.1f batch_size=%d candidate_count=%d "
        "blocking_count=%d cursor_before=%d cursor_after=%d wrap_count=%d "
        "lock_acquired=true reason=%s",
        float(scan["elapsed_ms"]),
        int(scan["batch_size"]),
        int(scan["candidate_count"]),
        int(scan["blocking_count"]),
        cursor_before,
        cursor_after,
        wrap_count,
        "candidate" if scan["candidate"] is not None else "scanned_no_candidate",
    )


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
