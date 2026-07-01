"""contextvars 上下文注入层。

通过 contextvars 自动传播 job_id / tid / forum_id / run_id / worker_id / trace_id，
让日志和诊断链路无需手工逐层传参。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_job_id: ContextVar[str] = ContextVar("job_id", default="")
_job_type: ContextVar[str] = ContextVar("job_type", default="")
_worker_id: ContextVar[str] = ContextVar("worker_id", default="")
_tid: ContextVar[int | None] = ContextVar("tid", default=None)
_forum_id: ContextVar[int | None] = ContextVar("forum_id", default=None)
_run_id: ContextVar[str] = ContextVar("run_id", default="")
_stage: ContextVar[str] = ContextVar("stage", default="")
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def new_trace_id() -> str:
    return uuid4().hex[:16]


class LogContext:
    """批量设置/恢复上下文，支持 ``with`` 语句自动清理。"""

    __slots__ = ("_tokens",)

    def __init__(self, **kwargs: Any):
        self._tokens: dict[str, Token] = {}
        for key, value in kwargs.items():
            var = _CONTEXT_VARS.get(key)
            if var is not None:
                self._tokens[key] = var.set(value)

    def __enter__(self) -> LogContext:
        return self

    def __exit__(self, *_: Any) -> None:
        for key, token in self._tokens.items():
            var = _CONTEXT_VARS.get(key)
            if var is not None:
                var.reset(token)


def get_log_context() -> dict[str, Any]:
    """读取当前全部上下文，用于注入日志记录。"""
    ctx: dict[str, Any] = {}
    trace = _trace_id.get()
    if trace:
        ctx["trace_id"] = trace
    job = _job_id.get()
    if job:
        ctx["job_id"] = job
    jt = _job_type.get()
    if jt:
        ctx["job_type"] = jt
    wid = _worker_id.get()
    if wid:
        ctx["worker_id"] = wid
    t = _tid.get()
    if t is not None:
        ctx["tid"] = t
    f = _forum_id.get()
    if f is not None:
        ctx["forum_id"] = f
    rid = _run_id.get()
    if rid:
        ctx["run_id"] = rid
    s = _stage.get()
    if s:
        ctx["stage"] = s
    cid = _correlation_id.get()
    if cid:
        ctx["correlation_id"] = cid
    return ctx


_CONTEXT_VARS: dict[str, ContextVar] = {
    "trace_id": _trace_id,
    "job_id": _job_id,
    "job_type": _job_type,
    "worker_id": _worker_id,
    "tid": _tid,
    "forum_id": _forum_id,
    "run_id": _run_id,
    "stage": _stage,
    "correlation_id": _correlation_id,
}
