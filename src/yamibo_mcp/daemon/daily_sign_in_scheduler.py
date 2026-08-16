from __future__ import annotations

import logging
import json
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.maintenance.sign_in_cache import is_fresh, read_sign_in_cache
from yamibo_mcp.yamibo.account_pool import get_account_identities

LOG = logging.getLogger(__name__)

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
SIGN_IN_HOUR = 1
SIGN_IN_RANDOM_WINDOW_MINUTES = 60


def maybe_enqueue_daily_sign_ins(
    repo: JobsRepository,
    settings: Settings,
    *,
    now: datetime | None = None,
    startup_check: bool = False,
) -> int:
    """Optionally check today's status on startup, then enqueue randomized sign-ins."""
    current = (now or datetime.now(LOCAL_TIMEZONE)).astimezone(LOCAL_TIMEZONE)
    local_day = current.date().isoformat()
    day_key = int(current.strftime("%Y%m%d"))
    created = 0
    identities = get_account_identities(settings)
    if startup_check:
        for identity in identities:
            if _has_daily_job(repo, day_key=day_key, account_id=identity.account_id, local_day=local_day, check_only=True):
                continue
            repo.create(
                JobType.DAILY_SIGN_IN.value,
                tid=day_key,
                payload={"account_id": identity.account_id, "local_day": local_day, "check_only": True},
                max_retries=1,
            )
            created += 1

    if current.hour < SIGN_IN_HOUR:
        return created

    sign_in_cache = read_sign_in_cache(settings)
    for identity in identities:
        if _has_daily_job(repo, day_key=day_key, account_id=identity.account_id, local_day=local_day, check_only=False):
            continue
        cache_entry = sign_in_cache.get(identity.account_id) or {}
        cache_data = cache_entry.get("data") if isinstance(cache_entry, dict) else None
        if is_fresh(cache_entry, now=current) and isinstance(cache_data, dict) and cache_data.get("today_status") == "checked":
            LOG.info("Skipping daily sign-in for already checked account_id=%s local_day=%s", identity.account_id, local_day)
            continue
        scheduled_at = scheduled_sign_in_time(local_day=local_day, account_id=identity.account_id)
        if current < scheduled_at:
            continue
        repo.create(
            JobType.DAILY_SIGN_IN.value,
            tid=day_key,
            payload={
                "account_id": identity.account_id,
                "local_day": local_day,
                "scheduled_at": scheduled_at.isoformat(),
            },
            max_retries=3,
        )
        created += 1
    if created:
        LOG.info("Enqueued %s daily sign-in job(s) for local_day=%s", created, local_day)
    return created


def scheduled_sign_in_time(*, local_day: str, account_id: str) -> datetime:
    """Return a restart-safe random minute in the first hour after 01:00."""
    day = datetime.fromisoformat(local_day).replace(tzinfo=LOCAL_TIMEZONE, hour=SIGN_IN_HOUR, minute=0, second=0, microsecond=0)
    seed = f"daily-sign-in:{local_day}:{account_id}"
    offset_minutes = random.Random(seed).randint(0, SIGN_IN_RANDOM_WINDOW_MINUTES - 1)
    return day + timedelta(minutes=offset_minutes)


def _has_daily_job(
    repo: JobsRepository,
    *,
    day_key: int,
    account_id: str,
    local_day: str,
    check_only: bool = False,
) -> bool:
    rows = repo.conn.execute(
        """
        SELECT payload_json
        FROM jobs
        WHERE job_type = ? AND tid = ? AND status IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            JobType.DAILY_SIGN_IN.value,
            day_key,
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
            JobStatus.RETRYING.value,
            JobStatus.PAUSED.value,
            JobStatus.INTERRUPTED.value,
            JobStatus.CANCEL_REQUESTED.value,
            JobStatus.SUCCEEDED.value,
            JobStatus.PARTIAL.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        ),
    ).fetchall()
    for row in rows:
        raw_payload = row["payload_json"] or {}
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
        if (
            payload.get("account_id") == account_id
            and payload.get("local_day") == local_day
            and bool(payload.get("check_only")) == check_only
        ):
            return True
    return False
