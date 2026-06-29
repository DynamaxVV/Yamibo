from __future__ import annotations

import logging
import re
import time
from threading import Lock
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
LOG = logging.getLogger(__name__)

_SLOW_QUERY_THRESHOLD_SECONDS = 0.5
_ENGINE_LOCK = Lock()
_ENGINE_METRICS: dict[str, dict[str, float | int]] = {}
_POOL_STATUS_RE = re.compile(
    r"Pool size:\s*(?P<pool_size>\d+)\s+Connections in pool:\s*(?P<connections_in_pool>\d+)\s+"
    r"Current Overflow:\s*(?P<current_overflow>-?\d+)\s+Current Checked out connections:\s*(?P<checked_out_connections>\d+)"
)


def install_engine_observability(engine: Engine) -> None:
    if getattr(engine, "_yamibo_observability_installed", False):
        return
    setattr(engine, "_yamibo_observability_installed", True)
    _metrics_for_engine(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(_conn, _cursor, statement, _parameters, context, _executemany) -> None:
        context._yamibo_query_started_at = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(_conn, _cursor, statement, _parameters, context, _executemany) -> None:
        started_at = getattr(context, "_yamibo_query_started_at", None)
        if started_at is None:
            return
        elapsed = time.perf_counter() - started_at
        if elapsed < _SLOW_QUERY_THRESHOLD_SECONDS:
            return
        metrics = _metrics_for_engine(engine)
        metrics["slow_query_count"] = int(metrics.get("slow_query_count", 0)) + 1
        metrics["slow_query_total_ms"] = float(metrics.get("slow_query_total_ms", 0.0)) + (elapsed * 1000.0)
        LOG.warning(
            "Slow SQL query backend=%s elapsed_ms=%.1f statement=%s",
            engine.url.get_backend_name(),
            elapsed * 1000.0,
            _summarize_statement(statement),
        )


def note_pool_timeout(engine: Engine) -> None:
    metrics = _metrics_for_engine(engine)
    metrics["pool_timeout_count"] = int(metrics.get("pool_timeout_count", 0)) + 1


def describe_engine_pool(engine: Engine | None) -> dict[str, Any]:
    if engine is None:
        return {}
    metrics = dict(_metrics_for_engine(engine))
    status = engine.pool.status()
    payload: dict[str, Any] = {"status": status, **metrics}
    match = _POOL_STATUS_RE.search(status)
    if match:
        payload.update({key: int(value) for key, value in match.groupdict().items()})
    return payload


def _metrics_for_engine(engine: Engine) -> dict[str, float | int]:
    key = str(id(engine))
    with _ENGINE_LOCK:
        metrics = _ENGINE_METRICS.get(key)
        if metrics is None:
            metrics = {
                "slow_query_count": 0,
                "slow_query_total_ms": 0.0,
                "pool_timeout_count": 0,
            }
            _ENGINE_METRICS[key] = metrics
        return metrics


def _summarize_statement(statement: Any) -> str:
    text = " ".join(str(statement).split())
    if len(text) <= 200:
        return text
    return text[:197] + "..."
