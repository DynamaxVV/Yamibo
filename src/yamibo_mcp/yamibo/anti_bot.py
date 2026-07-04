from __future__ import annotations

import logging
import urllib.error
from typing import Any

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.errors import RemoteAccessPausedError, RemoteFetchError, RemoteMaintenanceError
from yamibo_mcp.time_utils import utc_now_iso

LOG = logging.getLogger(__name__)


REMOTE_ACCESS_PAUSE_KEY = "remote_access_pause"
MAINTENANCE_PAUSE_KEY = "maintenance_pause"

# 需要远程访问论坛的任务类型 —— 维护或反爬暂停时统一收口于此。
REMOTE_JOB_TYPES = frozenset({
    JobType.SYNC_THREAD.value,
    JobType.UPDATE_THREAD.value,
    JobType.IMAGE_BACKFILL.value,
})
_LIVE_PAUSABLE_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.INTERRUPTED.value,
}


def is_http_444_error(exc: Exception) -> bool:
    """检测 HTTP 444 反爬拦截。

    HTTP 444（Nginx: connection closed without response）在不同 HTTP 库中有
    不同的表现形式。curl_cffi（HTTP/2）会将其报告为 curl error 92
    （CURLE_HTTP2_STREAM: PROTOCOL_ERROR），因为没有 HTTP 响应可解析。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 444
    if isinstance(exc, RemoteFetchError):
        details = getattr(exc, "details", None)
        if isinstance(details, dict):
            if details.get("status_code") == 444:
                return True
            # curl_cffi HTTP/2 下的 HTTP 444 表现：curl error 92 PROTOCOL_ERROR
            last_error_type = details.get("last_error_type", "")
            last_error_msg = details.get("last_error_message", "")
            if last_error_type == "RequestsError":
                lower_msg = last_error_msg.lower()
                # curl error 92: HTTP/2 stream not closed cleanly
                if "(92)" in last_error_msg or "http/2 stream" in lower_msg:
                    return True
                # curl error 56: RECV_ERROR (HTTP/1.1 下的连接重置)
                if "(56)" in last_error_msg and ("reset" in lower_msg or "connection" in lower_msg):
                    return True
        text = str(exc).lower()
        return "http error 444" in text or "status 444" in text
    return False


def is_http_429_error(exc: Exception) -> bool:
    """Check for HTTP 429 Too Many Requests (rate limiting)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429
    if isinstance(exc, RemoteFetchError):
        details = getattr(exc, "details", None)
        if isinstance(details, dict) and details.get("status_code") == 429:
            return True
        text = str(exc).lower()
        return "http error 429" in text or "status 429" in text
    return False


# Patterns that suggest a soft interception page (Cloudflare, etc.)
_SOFT_BLOCK_SIGNATURES = [
    "just a moment",
    "cf-browser-verification",
    "checking your browser",
    "attention required",
    "captcha",
    "challenge-platform",
]


def is_soft_block_page(html: str) -> bool:
    """Detect soft interception pages (Cloudflare challenge, CAPTCHA etc.)."""
    lower = html.lower()
    return any(sig in lower for sig in _SOFT_BLOCK_SIGNATURES)


def get_remote_access_pause_state(conn) -> dict[str, Any] | None:
    state = SystemStateRepository(conn).get_json(REMOTE_ACCESS_PAUSE_KEY)
    if not isinstance(state, dict) or not state.get("active"):
        return None
    return state


def ensure_remote_access_allowed(conn) -> None:
    state = get_remote_access_pause_state(conn)
    if state is None:
        return
    triggered_at = state.get("triggered_at") or "unknown"
    source = state.get("source") or "unknown"
    raise RemoteAccessPausedError(
        f"remote access paused after HTTP 444 anti-bot detection at {triggered_at} ({source})",
        details=state,
    )


def activate_remote_access_pause(
    conn,
    *,
    source: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jobs_repo = JobsRepository(conn)
    paused_job_ids: list[str] = []
    for job in jobs_repo.list(limit=None):
        if job.job_type not in REMOTE_JOB_TYPES or job.status not in _LIVE_PAUSABLE_JOB_STATUSES:
            continue
        if jobs_repo.pause(job.job_id):
            paused_job_ids.append(job.job_id)
    state = {
        "active": True,
        "reason": "http_444",
        "message": message,
        "source": source,
        "triggered_at": utc_now_iso(),
        "paused_job_ids": paused_job_ids,
        "paused_job_count": len(paused_job_ids),
        "job_types": list(REMOTE_JOB_TYPES),
        "context": context or {},
    }
    SystemStateRepository(conn).set_json(REMOTE_ACCESS_PAUSE_KEY, state)
    return state


def clear_remote_access_pause(conn, *, resume_jobs: bool = True) -> dict[str, Any]:
    jobs_repo = JobsRepository(conn)
    resumed_job_ids: list[str] = []
    if resume_jobs:
        for job in jobs_repo.list(limit=None, status=JobStatus.PAUSED):
            if job.job_type not in REMOTE_JOB_TYPES:
                continue
            if jobs_repo.resume(job.job_id):
                resumed_job_ids.append(job.job_id)
    SystemStateRepository(conn).delete(REMOTE_ACCESS_PAUSE_KEY)
    return {
        "ok": True,
        "resumed_job_ids": resumed_job_ids,
        "resumed_job_count": len(resumed_job_ids),
    }


# ── 论坛维护暂停 ──────────────────────────────────────────────


def get_maintenance_pause_state(conn) -> dict[str, Any] | None:
    """返回当前维护暂停状态，如果未激活则返回 None。"""
    state = SystemStateRepository(conn).get_json(MAINTENANCE_PAUSE_KEY)
    if not isinstance(state, dict) or not state.get("active"):
        return None
    return state


def ensure_no_maintenance_pause(conn) -> None:
    """如果维护暂停激活则直接抛错，避免发起远程请求。"""
    state = get_maintenance_pause_state(conn)
    if state is None:
        return
    triggered_at = state.get("triggered_at") or "unknown"
    raise RemoteMaintenanceError(
        f"forum maintenance detected at {triggered_at}, remote jobs paused",
    )


def activate_maintenance_pause(
    conn,
    *,
    source: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """检测到维护页后激活全局暂停，暂停所有远程任务。"""
    state_repo = SystemStateRepository(conn)
    existing = get_maintenance_pause_state(conn)
    if existing is not None:
        return existing

    jobs_repo = JobsRepository(conn)
    paused_job_ids: list[str] = []
    for job in jobs_repo.list(limit=None):
        if job.job_type not in REMOTE_JOB_TYPES or job.status not in _LIVE_PAUSABLE_JOB_STATUSES:
            continue
        if jobs_repo.pause(job.job_id):
            paused_job_ids.append(job.job_id)

    state = {
        "active": True,
        "reason": "forum_maintenance",
        "source": source,
        "triggered_at": utc_now_iso(),
        "last_probe_at": None,
        "probe_failures": 0,
        "paused_job_ids": paused_job_ids,
        "paused_job_count": len(paused_job_ids),
        "job_types": list(REMOTE_JOB_TYPES),
        "context": context or {},
    }
    state_repo.set_json(MAINTENANCE_PAUSE_KEY, state)
    return state


def clear_maintenance_pause(conn, *, resume_jobs: bool = True) -> dict[str, Any]:
    """维护结束，清除暂停状态并恢复任务。"""
    jobs_repo = JobsRepository(conn)
    resumed_job_ids: list[str] = []
    if resume_jobs:
        for job in jobs_repo.list(limit=None, status=JobStatus.PAUSED):
            if job.job_type not in REMOTE_JOB_TYPES:
                continue
            if jobs_repo.resume(job.job_id):
                resumed_job_ids.append(job.job_id)
    SystemStateRepository(conn).delete(MAINTENANCE_PAUSE_KEY)
    return {
        "ok": True,
        "resumed_job_ids": resumed_job_ids,
        "resumed_job_count": len(resumed_job_ids),
    }


def record_maintenance_probe_success(conn) -> dict[str, Any]:
    """记录一次成功的维护探测，清除暂停状态。"""
    return clear_maintenance_pause(conn, resume_jobs=True)


def record_maintenance_probe_failure(conn) -> dict[str, Any] | None:
    """记录一次失败的维护探测，更新失败计数。"""
    state = get_maintenance_pause_state(conn)
    if state is None:
        return None
    state["last_probe_at"] = utc_now_iso()
    state["probe_failures"] = state.get("probe_failures", 0) + 1
    SystemStateRepository(conn).set_json(MAINTENANCE_PAUSE_KEY, state)
    return state


# 维护探测间隔（秒）—— 10 分钟
_MAINTENANCE_PROBE_INTERVAL_SECONDS = 600


def _time_since_last_probe(state: dict[str, Any] | None) -> float | None:
    """返回距离上次探测的秒数，如果尚未探测过则返回 None。"""
    if state is None or state.get("last_probe_at") is None:
        return None
    from datetime import datetime, timezone
    try:
        last = datetime.fromisoformat(state["last_probe_at"])
        return (datetime.now(timezone.utc) - last).total_seconds()
    except (ValueError, TypeError):
        return None


def should_probe_maintenance(conn) -> bool:
    """是否需要发起一次维护探测。"""
    state = get_maintenance_pause_state(conn)
    if state is None:
        return False
    elapsed = _time_since_last_probe(state)
    return elapsed is None or elapsed >= _MAINTENANCE_PROBE_INTERVAL_SECONDS


def probe_maintenance(*, cookie_file: str | None, settings) -> bool:
    """用一次轻量 HTTP 请求探测维护是否结束。

    返回 True 表示维护已结束（请求正常），False 表示维护仍在继续。
    不做登录、不触发反爬机制，任何非维护错误都视为维护结束。
    """
    from yamibo_mcp.yamibo.client import YamiboClient
    from yamibo_mcp.yamibo.page_classifier import PageType, classify_html
    from yamibo_mcp.yamibo.urls import forum_page_url, DEFAULT_FORUM_ID
    try:
        client = YamiboClient(
            timeout=10.0,
            retries=0,
            cookie_file=cookie_file,
        )
        result = client._open_html(
            forum_page_url(page=1, forum_id=DEFAULT_FORUM_ID),
            referer="https://bbs.yamibo.com/",
        )
        classification = classify_html(result.html)
        if classification.page_type == PageType.REMOTE_MAINTENANCE:
            LOG.info("Maintenance probe: still in maintenance")
            return False
        LOG.info("Maintenance probe: page type=%s, maintenance appears over", classification.page_type.value)
        return True
    except Exception:
        LOG.info("Maintenance probe: request failed (non-maintenance error), treating as over", exc_info=True)
        return True
