from __future__ import annotations

import collections
from datetime import UTC, datetime
import json
import logging
import threading
from typing import Any

from yamibo_mcp.structured_logging import StructuredJSONFormatter


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    stripped = value.strip()
    try:
        return float(stripped)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class LogBuffer(logging.Handler):
    """In-memory ring buffer that captures structured log records for the web UI."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self._buffer: collections.deque[dict] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            entry = json.loads(message)
            if isinstance(entry, dict):
                with self._lock:
                    self._buffer.append(entry)
        except Exception:
            pass

    def get_recent(
        self,
        limit: int = 100,
        since_ts: str | None = None,
        *,
        job_id: str | None = None,
        tid: int | None = None,
        event_type: str | None = None,
        level: str | None = None,
        component: str | None = None,
        query: str | None = None,
        errors_only: bool = False,
    ) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._buffer)
        if since_ts is not None:
            since_value = _timestamp(since_ts)
            if since_value is not None:
                entries = [e for e in entries if (entry_ts := _timestamp(e.get("ts"))) is not None and entry_ts > since_value]
        if job_id is not None:
            entries = [e for e in entries if str(e.get("job_id") or "") == job_id]
        if tid is not None:
            entries = [e for e in entries if e.get("tid") == tid]
        if event_type is not None:
            entries = [e for e in entries if str(e.get("event_type") or "") == event_type]
        if level is not None:
            entries = [e for e in entries if str(e.get("level") or "").upper() == level.upper()]
        if component is not None:
            needle = component.casefold()
            entries = [e for e in entries if needle in str(e.get("component") or "").casefold()]
        if errors_only:
            entries = [
                e
                for e in entries
                if str(e.get("level") or "").upper() in {"ERROR", "CRITICAL"}
                or str(e.get("result") or "").lower() in {"failure", "partial", "blocked"}
                or str(e.get("status") or "").lower() in {"error", "partial", "blocked", "missing"}
                or bool(e.get("error_code"))
            ]
        if query is not None:
            needle = query.casefold()
            entries = [
                e
                for e in entries
                if needle in json.dumps(e, ensure_ascii=False, default=str).casefold()
            ]
        return entries[-limit:]


_buffer: LogBuffer | None = None


def get_log_buffer() -> LogBuffer:
    global _buffer
    if _buffer is None:
        _buffer = LogBuffer(capacity=500)
        _buffer.setFormatter(StructuredJSONFormatter())
        root = logging.getLogger()
        if not any(getattr(handler, "_yamibo_logging_handler", False) and isinstance(handler, LogBuffer) for handler in root.handlers):
            _buffer._yamibo_logging_handler = True  # type: ignore[attr-defined]
            root.addHandler(_buffer)
    return _buffer
