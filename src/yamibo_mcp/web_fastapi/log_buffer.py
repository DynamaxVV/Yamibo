from __future__ import annotations

import collections
import json
import logging
import threading

from yamibo_mcp.structured_logging import StructuredJSONFormatter


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

    def get_recent(self, limit: int = 100, since_ts: float | None = None) -> list[dict]:
        with self._lock:
            entries = list(self._buffer)
        if since_ts is not None:
            entries = [e for e in entries if isinstance(e.get("ts"), str) and e["ts"] > since_ts]
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
