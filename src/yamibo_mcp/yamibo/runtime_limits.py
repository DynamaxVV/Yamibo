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
    with state.lock:
        elapsed = time.monotonic() - state.last_request_time
        jitter = random.uniform(0, request_interval_jitter) if request_interval_jitter > 0 else 0
        wait = request_interval + jitter - elapsed
        if wait > 0:
            time.sleep(wait)
        state.last_request_time = time.monotonic()


@contextmanager
def acquire_cookie_download_slot(cookie_file: str | Path | None) -> Iterator[None]:
    state = _state_for(cookie_file)
    state.download_slots.acquire()
    try:
        yield
    finally:
        state.download_slots.release()
