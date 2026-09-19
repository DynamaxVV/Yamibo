from __future__ import annotations

import logging
import threading
import time
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
    JobType.DAILY_SIGN_IN.value,
})
_LIVE_PAUSABLE_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.INTERRUPTED.value,
}

# curl_cffi 的 RequestsError 是 RequestException 的别名，实际抛出的异常类名是
# RequestException 或其子类（ConnectionError 等）。client.py 存 __class__.__name__ 字符串，
# 这里用集合匹配覆盖所有可能。
_CURL_CFFI_REQUEST_ERROR_NAMES = frozenset({
    "RequestsError",
    "RequestException",
    "ConnectionError",
    "Timeout",
    "HTTPError",
})


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
            # PONETAIL: curl_cffi.requests.errors.RequestsError 是 RequestException 的别名，
            # isinstance(exc, RequestsError) 为 True 但 __class__.__name__ 是 "RequestException"
            # 或其子类（如 "ConnectionError"）。client.py 存的是 __name__ 字符串，所以这里用集合匹配。
            if last_error_type in _CURL_CFFI_REQUEST_ERROR_NAMES:
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


# ── HTTP 444 渐进式响应（daemon + application 共享） ──────────────────────
#
# 在 5 分钟滑动窗口内累计 3 次 444 才触发全局暂停；未达阈值时只清代理缓存
# 换节点重试。状态持久化到 system_state，进程重启可恢复（monotonic 时间戳
# 会过期，但重启后会按窗口自然清理）。daemon 与 application（MCP/CLI 同步
# 入口）共用同一计数器与阈值，避免两条路径策略割裂。

_444_KEY = "anti_bot_444_events"
_444_EVENTS: list[float] = []
_444_LOCK = threading.Lock()
_444_THRESHOLD = 3
_444_WINDOW_SECONDS = 300  # 5 minutes


def _restore_444_events(conn) -> None:
    """从 system_state 恢复 444 事件计数（进程启动时调用）。"""
    state = SystemStateRepository(conn).get_json(_444_KEY)
    if not isinstance(state, dict):
        return
    timestamps = state.get("timestamps", [])
    if not isinstance(timestamps, list):
        return
    now = time.monotonic()
    with _444_LOCK:
        _444_EVENTS[:] = [float(t) for t in timestamps if isinstance(t, (int, float)) and now - float(t) < _444_WINDOW_SECONDS]


def _record_444(conn) -> bool:
    """记录一次 444 事件。持久化到 system_state。返回 True 表示已超阈值。"""
    now = time.monotonic()
    with _444_LOCK:
        _444_EVENTS[:] = [t for t in _444_EVENTS if now - t < _444_WINDOW_SECONDS]
        _444_EVENTS.append(now)
        exceeded = len(_444_EVENTS) >= _444_THRESHOLD
        try:
            SystemStateRepository(conn).set_json(_444_KEY, {"timestamps": _444_EVENTS, "threshold": _444_THRESHOLD, "window_seconds": _444_WINDOW_SECONDS})
        except Exception:
            pass  # 持久化是 best-effort，不能影响 handler 主流程
        return exceeded


def handle_http_444(
    conn,
    *,
    source: str,
    exc: Exception,
    context: dict[str, Any] | None = None,
    node: str | None = None,
    settings=None,
) -> bool:
    """统一的 HTTP 444 响应入口。

    节点级优先：若提供 node，把该节点加入 444 黑名单（临时拉黑），
    所有可用节点都被拉黑时才触发全局暂停；否则返回 False 让调用方换节点重试。

    无 node 时（如 application 层无 proxy_pool 配置）退化为全局阈值：5 分钟内
    累计 3 次 444 即暂停。

    Args:
        conn: 已打开的 DatabaseConnection。
        source: 调用方标识。
        exc: 捕获到的异常（写入暂停上下文）。
        context: 额外上下文。
        node: 触发 444 的代理节点名；None 则走全局阈值。
        settings: Settings，节点级路径需要它来发现可用节点集合。

    Returns:
        True 表示已触发全局暂停；False 表示应换节点重试。
    """
    from yamibo_mcp.yamibo.proxy_pool import (
        all_nodes_blacklisted,
        clear_proxy_cache,
        mark_node_444,
    )

    cleared = clear_proxy_cache()
    if cleared:
        LOG.info("Cleared %d proxy cache entries after HTTP 444 from %s", cleared, source)

    # 节点级路径：拉黑该节点，全部可用节点都拉黑才全局暂停
    if node is not None and settings is not None:
        mark_node_444(node)
        if not all_nodes_blacklisted(settings):
            LOG.warning(
                "HTTP 444 from %s on node %s — node blacklisted, will retry with different node",
                source, node,
            )
            return False
        # 全部节点都在黑名单 → 全局暂停
    else:
        # 无 node 信息：退化为全局阈值计数
        if not _record_444(conn):
            LOG.warning(
                "HTTP 444 from %s — threshold not yet reached (%d/%d within %ds), will retry with different node",
                source, len(_444_EVENTS), _444_THRESHOLD, _444_WINDOW_SECONDS,
            )
            return False

    remote_fetch_details = {}
    if isinstance(exc, RemoteFetchError):
        remote_fetch_details = getattr(exc, "details", {}) or {}
    pause_context = {**(context or {}), "remote_fetch": remote_fetch_details}
    if node is not None:
        pause_context["trigger_node"] = node
    state = activate_remote_access_pause(
        conn,
        source=source,
        message="Yamibo returned HTTP 444 from all proxy nodes (or global threshold reached). Remote access paused.",
        context=pause_context,
    )
    LOG.warning("Paused remote archive/update jobs after HTTP 444 from %s (node=%s): %s", source, node, state)
    return True


# Patterns that suggest a soft interception page (Cloudflare, etc.)
_SOFT_BLOCK_SIGNATURES = [
    "just a moment",
    "cf-browser-verification",
    "checking your browser",
    "attention required",
    "sorry, you have been blocked",
    "captcha",
    "challenge-platform",
    # Baidu WAF's JavaScript challenge currently returned by bbs.yamibo.com.
    "__noxexpire",
    "gangplank_",
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
        "last_probe_at": None,
        "probe_failures": 0,
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


# ── 444 暂停恢复探测 ─────────────────────────────────────────────
#
# 镜像维护探测：daemon 每 10 分钟用轻量请求探一下论坛，确认反爬已解除后才
# 清除暂停。比维护探测更保守——必须明确拿到正常页面（FORUM_LIST/THREAD/
# SEARCH）才恢复；444、soft-block、维护页、未知页、网络错误都不恢复，避免
# 刚解禁又立刻再触发 444 暂停。

_REMOTE_ACCESS_PROBE_INTERVAL_SECONDS = 600  # 10 分钟


def record_remote_access_probe_success(conn) -> dict[str, Any]:
    """记录一次成功的 444 探测，清除暂停并恢复任务。"""
    return clear_remote_access_pause(conn, resume_jobs=True)


def record_remote_access_probe_failure(conn) -> dict[str, Any] | None:
    """记录一次失败的 444 探测，更新失败计数。"""
    state = get_remote_access_pause_state(conn)
    if state is None:
        return None
    state["last_probe_at"] = utc_now_iso()
    state["probe_failures"] = state.get("probe_failures", 0) + 1
    SystemStateRepository(conn).set_json(REMOTE_ACCESS_PAUSE_KEY, state)
    return state


def should_probe_remote_access(conn) -> bool:
    """是否需要发起一次 444 暂停恢复探测。"""
    state = get_remote_access_pause_state(conn)
    if state is None:
        return False
    elapsed = _time_since_last_probe(state)
    return elapsed is None or elapsed >= _REMOTE_ACCESS_PROBE_INTERVAL_SECONDS


def probe_remote_access(*, cookie_file: str | None, settings) -> bool:
    """用一次轻量 HTTP 请求探测 444 反爬是否已解除。

    返回 True 表示反爬已解除（拿到正常论坛页面），False 表示仍被封。
    判定比维护探测更严格：只有明确分类为正常页面类型才返回 True；
    444、soft-block、维护页、登录页、未知页、任何异常都返回 False。
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
        normal_types = {PageType.FORUM_LIST, PageType.THREAD_DETAIL, PageType.SEARCH_RESULT}
        if classification.page_type in normal_types:
            LOG.info("444 probe: page type=%s, anti-bot appears lifted", classification.page_type.value)
            return True
        LOG.info("444 probe: page type=%s (not normal), still blocked", classification.page_type.value)
        return False
    except Exception as exc:
        # 444 / soft-block / 网络错误 都视为仍被封
        if is_http_444_error(exc):
            LOG.info("444 probe: still getting HTTP 444")
        else:
            LOG.info("444 probe: request failed (%s), treating as still blocked", exc.__class__.__name__)
        return False


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
