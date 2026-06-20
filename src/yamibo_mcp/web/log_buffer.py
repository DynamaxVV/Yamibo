from __future__ import annotations

import collections
import logging
import threading
import time


class LogBuffer(logging.Handler):
    """In-memory ring buffer that captures log records for the web UI."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self._buffer: collections.deque[dict] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": self.format(record),
            }
            with self._lock:
                self._buffer.append(entry)
        except Exception:
            pass

    def get_recent(self, limit: int = 100, since_ts: float | None = None) -> list[dict]:
        with self._lock:
            entries = list(self._buffer)
        if since_ts is not None:
            entries = [e for e in entries if e["ts"] > since_ts]
        return entries[-limit:]


_buffer: LogBuffer | None = None


def get_log_buffer() -> LogBuffer:
    global _buffer
    if _buffer is None:
        _buffer = LogBuffer(capacity=500)
        _buffer.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(_buffer)
        logging.getLogger().setLevel(logging.INFO)
    return _buffer
