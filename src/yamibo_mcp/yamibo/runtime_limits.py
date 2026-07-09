from __future__ import annotations

import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, Semaphore
from typing import Iterator

_COOKIE_DOWNLOAD_LIMIT = 5


def cookie_limit_key(cookie_file: str | Path | None) -> str:
    if cookie_file is None:
        return "__default__"
    return str(Path(cookie_file).expanduser().resolve(strict=False))


@dataclass
class _CookieRuntimeState:
    lock: Lock = field(default_factory=Lock)
    last_request_time: float = 0.0
    download_slots: Semaphore = field(default_factory=lambda: Semaphore(_COOKIE_DOWNLOAD_LIMIT))


_STATE_REGISTRY: dict[str, _CookieRuntimeState] = {}
_REGISTRY_LOCK = Lock()


class CookieDownloadSlotTimeoutError(TimeoutError):
    pass


def _state_for(cookie_file: str | Path | None) -> _CookieRuntimeState:
    key = cookie_limit_key(cookie_file)
    with _REGISTRY_LOCK:
        state = _STATE_REGISTRY.get(key)
        if state is None:
            state = _CookieRuntimeState()
            _STATE_REGISTRY[key] = state
        return state


def throttle_cookie_request(cookie_file: str | Path | None, *, request_interval: float, request_interval_jitter: float) -> None:
    if request_interval <= 0:
        return
    state = _state_for(cookie_file)
    sleep_seconds = 0.0
    with state.lock:
        now = time.monotonic()
        jitter = random.uniform(0, request_interval_jitter) if request_interval_jitter > 0 else 0
        if state.last_request_time > now:
            sleep_seconds = state.last_request_time - now
            next_request_time = state.last_request_time + request_interval + jitter
        else:
            next_request_time = now + request_interval + jitter
        state.last_request_time = next_request_time
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)


@contextmanager
def acquire_cookie_download_slot(cookie_file: str | Path | None, *, timeout: float | None = None) -> Iterator[None]:
    state = _state_for(cookie_file)
    acquired = state.download_slots.acquire() if timeout is None else state.download_slots.acquire(timeout=max(timeout, 0.0))
    if not acquired:
        raise CookieDownloadSlotTimeoutError(f"timed out waiting for cookie download slot after {timeout} seconds")
    try:
        yield
    finally:
        state.download_slots.release()
