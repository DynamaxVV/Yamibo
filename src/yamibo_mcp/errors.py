from __future__ import annotations

import re
from typing import Any


class YamiboError(Exception):
    """Base application error."""


class JobNotFound(YamiboError):
    """Raised when a job cannot be found."""


class IdempotencyConflict(YamiboError):
    """Raised when an idempotency key is reused for another request."""


class LeaseNotAcquired(YamiboError):
    """Raised when a worker cannot acquire a job lease."""


class RemoteFetchError(YamiboError):
    """Raised when remote HTML cannot be fetched."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class LoginRequiredError(RemoteFetchError):
    """Raised when Yamibo returns a login-required page."""


class RemoteMaintenanceError(RemoteFetchError):
    """Raised when Yamibo is in maintenance mode."""


class RemoteAccessPausedError(RemoteFetchError):
    """Raised when remote access is paused after anti-bot detection."""


class UnsupportedDatabaseBackendError(YamiboError):
    """Raised when a feature is not available on the current database backend."""


class UnexpectedPageError(RemoteFetchError):
    """Raised when fetched HTML is not the expected thread detail page."""


class ThreadPermissionRequiredError(UnexpectedPageError):
    """Raised when a thread page requires a higher read permission."""

    def __init__(self, message: str, *, required_permission: int | None = None, details: dict[str, Any] | None = None):
        merged_details = dict(details or {})
        if required_permission is not None:
            merged_details["required_permission"] = required_permission
        super().__init__(message, details=merged_details)
        self.required_permission = required_permission


# ── 错误分类 ──────────────────────────────────────────────
# daemon 和 agent adapter 共用，将异常映射为结构化的 error_code，
# 避免直接用 exc.__class__.__name__ 导致不同根因被归入同一个桶。


def classify_error(exc: BaseException) -> str:
    """从异常对象提取结构化 error_code。

    子类检查必须排在父类之前，避免 UnexpectedPageError / LoginRequiredError
    等被 RemoteFetchError 分支吞掉。
    """
    msg = str(exc)

    # —— 子类优先 ——
    if isinstance(exc, LoginRequiredError):
        return "REMOTE_LOGIN_REQUIRED"

    if isinstance(exc, RemoteMaintenanceError):
        return "REMOTE_MAINTENANCE"

    if isinstance(exc, RemoteAccessPausedError):
        return "REMOTE_ACCESS_PAUSED"

    if isinstance(exc, ThreadPermissionRequiredError):
        return "REMOTE_THREAD_PERMISSION_REQUIRED"

    if isinstance(exc, UnexpectedPageError):
        if "本帖已经删除" in msg:
            code = _extract_permission_code(msg)
            # 255 = 真删除；30/40/50/100 等 = 权限不足，可换高权限号重试
            if code == 255:
                return "THREAD_DELETED"
            return "REMOTE_THREAD_PERMISSION_REQUIRED"
        if "没有权限访问该群组" in msg:
            return "GROUP_ACCESS_DENIED"
        if "您需要升级您所在的用户组" in msg:
            return "REMOTE_THREAD_PERMISSION_REQUIRED"
        return "UNEXPECTED_REMOTE_PAGE"

    # —— RemoteFetchError 兜底 ——
    if isinstance(exc, RemoteFetchError):
        details = getattr(exc, "details", None) or {}
        status_code = details.get("status_code")
        last_error_type = details.get("last_error_type", "")
        last_error_msg = details.get("last_error_message", "")

        if _has_soft_block(msg, last_error_msg):
            return "REMOTE_SOFT_BLOCK"

        if status_code == 404 or "HTTP Error 404" in last_error_msg:
            return "REMOTE_HTTP_404"
        if status_code == 403 or "HTTP Error 403" in last_error_msg:
            return "REMOTE_HTTP_403"
        if status_code is not None and 500 <= status_code < 600:
            return "REMOTE_HTTP_5XX"
        if "HTTP Error" in last_error_msg or "HTTP Error" in msg:
            return "REMOTE_HTTP_XXX"

        if last_error_type == "TimeoutError" or _has_timeout(msg, last_error_msg):
            return "REMOTE_TIMEOUT"

        if last_error_type in ("URLError", "RemoteDisconnected") or _has_connection_error(msg, last_error_msg):
            return "REMOTE_CONNECTION_ERROR"

        return "REMOTE_FETCH_FAILED"

    # —— 非远程异常 ——
    if isinstance(exc, ValueError):
        return "INVALID_ARGUMENT"

    if isinstance(exc, FileNotFoundError):
        return "LOCAL_ARCHIVE_NOT_FOUND"

    return "INTERNAL_ERROR"


def reclassify_error_code(old_error_code: str, error_message: str | None) -> str:
    """对已入库的失败任务重新分类，基于旧的 error_code 和 error_message。"""
    msg = error_message or ""

    if old_error_code == "RemoteFetchError":
        if _has_soft_block(msg):
            return "REMOTE_SOFT_BLOCK"
        if "HTTP Error 404" in msg:
            return "REMOTE_HTTP_404"
        if "HTTP Error 403" in msg:
            return "REMOTE_HTTP_403"
        if re.search(r"HTTP Error 5\d\d", msg):
            return "REMOTE_HTTP_5XX"
        if "HTTP Error" in msg:
            return "REMOTE_HTTP_XXX"
        if _has_timeout(msg):
            return "REMOTE_TIMEOUT"
        if _has_connection_error(msg):
            return "REMOTE_CONNECTION_ERROR"
        return "REMOTE_FETCH_FAILED"

    if old_error_code == "UnexpectedPageError" or old_error_code == "THREAD_DELETED":
        if "本帖已经删除" in msg:
            code = _extract_permission_code(msg)
            if code == 255:
                return "THREAD_DELETED"
            return "REMOTE_THREAD_PERMISSION_REQUIRED"
        if "没有权限访问该群组" in msg:
            return "GROUP_ACCESS_DENIED"
        if "您需要升级您所在的用户组" in msg:
            return "REMOTE_THREAD_PERMISSION_REQUIRED"
        return "UNEXPECTED_REMOTE_PAGE"

    if old_error_code == "LoginRequiredError":
        return "REMOTE_LOGIN_REQUIRED"

    # 已经是细粒度 code 或无法识别的，原样返回
    return old_error_code


# ── 辅助检测函数 ──────────────────────────────────────────


def _has_soft_block(*texts: str) -> bool:
    return any("soft block" in t.lower() or "cf challenge" in t.lower() or "captcha" in t.lower() for t in texts)


def _has_timeout(*texts: str) -> bool:
    """检测真正的超时错误，避免误匹配参数描述中的 timeout= 字样。"""
    return any("timed out" in t.lower() for t in texts)


def _has_connection_error(*texts: str) -> bool:
    keywords = ("connection refused", "connection reset", "connection aborted", "no route to host", "name or service not known")
    return any(
        "connection" in t.lower() or any(kw in t.lower() for kw in keywords)
        for t in texts
    )


def _extract_permission_code(msg: str) -> int | None:
    """从 Discuz 提示语中提取权限代码，如 '错误权限代码255' → 255。"""
    m = re.search(r"错误权限代码(\d+)", msg)
    return int(m.group(1)) if m else None
