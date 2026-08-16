from __future__ import annotations

import logging
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from yamibo_mcp.config import Settings
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.yamibo.account_pool import get_account_identities

LOG = logging.getLogger(__name__)

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
SIGN_IN_HOUR = 1


def maybe_enqueue_daily_sign_ins(
    repo: JobsRepository,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """After 01:00 local time, enqueue at most one sign-in job per account/day."""
    current = (now or datetime.now(LOCAL_TIMEZONE)).astimezone(LOCAL_TIMEZONE)
    if current.hour < SIGN_IN_HOUR:
        return 0
    local_day = current.date().isoformat()
    day_key = int(current.strftime("%Y%m%d"))
    created = 0
    for identity in get_account_identities(settings):
        if _has_daily_job(repo, day_key=day_key, account_id=identity.account_id, local_day=local_day):
            continue
        repo.create(
            JobType.DAILY_SIGN_IN.value,
            tid=day_key,
            payload={"account_id": identity.account_id, "local_day": local_day},
            max_retries=3,
        )
        created += 1
    if created:
        LOG.info("Enqueued %s daily sign-in job(s) for local_day=%s", created, local_day)
    return created


def _has_daily_job(repo: JobsRepository, *, day_key: int, account_id: str, local_day: str) -> bool:
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
        if payload.get("account_id") == account_id and payload.get("local_day") == local_day:
            return True
    return False
