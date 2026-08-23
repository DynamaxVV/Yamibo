from __future__ import annotations

import logging

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.models import Job
from yamibo_mcp.maintenance.sign_in_cache import update_sign_in_cache_account
from yamibo_mcp.yamibo.sign_in import run_daily_sign_in

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
    check_only = bool(job.payload.get("check_only"))
    sign_in = run_daily_sign_in(settings, account_id=account_id, check_only=check_only)
    identity = sign_in.identity
    result = sign_in.result
    profile = sign_in.profile
    if check_only:
        update_sign_in_cache_account(
            settings,
            identity.account_id,
            profile or {"today_status": "not_checked"},
            refresh_timestamp=True,
        )
        repo.succeed(
            job.job_id,
            artifacts={
                "local_day": day,
                "account_id": identity.account_id,
                "check_only": True,
                "status_code": result.status_code,
                "remote_transport": sign_in.transport,
                "proxy_pool_node": sign_in.proxy_binding.node if sign_in.proxy_binding else None,
                "direct_fallback_error": sign_in.direct_fallback_error,
            },
        )
        LOG.info(
            "Daily sign-in status checked for account_id=%s local_day=%s transport=%s",
            identity.account_id,
            day,
            sign_in.transport,
        )
        return
    update_sign_in_cache_account(
        settings,
        identity.account_id,
        profile or {"today_status": "checked"},
        refresh_timestamp=profile is not None,
    )
    repo.succeed(
        job.job_id,
        artifacts={
            "local_day": day,
            "account_id": identity.account_id,
            "action_url": result.final_url,
            "status_code": result.status_code,
            "remote_transport": sign_in.transport,
            "proxy_pool_node": sign_in.proxy_binding.node if sign_in.proxy_binding else None,
            "direct_fallback_error": sign_in.direct_fallback_error,
        },
    )
    LOG.info(
        "Daily sign-in succeeded for account_id=%s local_day=%s transport=%s",
        identity.account_id,
        day,
        sign_in.transport,
    )
