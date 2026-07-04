from __future__ import annotations

import json
import threading
import time


class TTLCache:
    """Thread-safe in-memory cache with per-entry TTL."""

    __slots__ = ("_data", "_lock", "_ttl")

    def __init__(self, ttl_seconds: float = 3.0):
        self._data: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: object) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


def jsonish_loads(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)
