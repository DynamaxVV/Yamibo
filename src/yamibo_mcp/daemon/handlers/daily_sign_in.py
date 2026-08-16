from __future__ import annotations

import logging

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.models import Job
from yamibo_mcp.maintenance.sign_in_cache import update_sign_in_cache_account
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client
from yamibo_mcp.yamibo.client import parse_daily_checkin_profile
from yamibo_mcp.yamibo.proxy_pool import select_random_proxy

LOG = logging.getLogger(__name__)


def handle_daily_sign_in(
    repo: JobsRepository,
    job: Job,
    worker_id: str,
    lease_seconds: int,
    settings,
) -> None:
    del worker_id, lease_seconds
    account_id = str(job.payload.get("account_id") or "default")
    day = str(job.payload.get("local_day") or "unknown")
    proxy_binding = select_random_proxy(settings)
    with borrow_yamibo_client(
        settings,
        account_id=account_id,
        proxy_url=proxy_binding.proxy_url if proxy_binding else None,
    ) as (identity, client):
        result = client.sign_daily_checkin()
    update_sign_in_cache_account(
        settings,
        identity.account_id,
        parse_daily_checkin_profile(getattr(result, "html", "")) or {"today_status": "checked"},
    )
    repo.succeed(
        job.job_id,
        artifacts={
            "local_day": day,
            "account_id": identity.account_id,
            "action_url": result.final_url,
            "status_code": result.status_code,
            "proxy_pool_node": proxy_binding.node if proxy_binding else None,
        },
    )
    LOG.info("Daily sign-in succeeded for account_id=%s local_day=%s", identity.account_id, day)
