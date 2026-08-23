from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from yamibo_mcp.config import AccountConfig, Settings
from yamibo_mcp.yamibo.client import YamiboClient

LOG = logging.getLogger(__name__)

# Per-cookie-file last refresh timestamp (UTC epoch seconds).
_REFRESH_REGISTRY: dict[str, float] = {}
_REFRESH_LOCK = Lock()
_ACCOUNT_PENALTIES: dict[str, tuple[float, float]] = {}  # account_id -> (score, cooldown_until)
_ACCOUNT_PENALTY_LOCK = Lock()
_ACCOUNT_COOLDOWN_SECONDS = {
    "soft_block": 45.0,
    "timeout": 20.0,
    "connection_error": 20.0,
    "rate_limited": 10.0,
}


@dataclass(frozen=True)
class AccountIdentity:
    account_id: str
    username: str | None
    password: str | None
    cookie_file: str
    enabled: bool
    weight: int
    permission_level: int
    request_interval_seconds: float
    request_interval_jitter_seconds: float
    max_concurrent_leases: int
    login_mode: str


def record_account_outcome(account_id: str | None, outcome: str) -> None:
    """Apply short-lived feedback to account selection without persisting health state."""
    if not account_id:
        return
    if outcome in {"success", "permission_required", "login_required"}:
        with _ACCOUNT_PENALTY_LOCK:
            _ACCOUNT_PENALTIES.pop(account_id, None)
        return
    cooldown = _ACCOUNT_COOLDOWN_SECONDS.get(outcome)
    if cooldown is None:
        return
    weight = {"soft_block": 3.0, "timeout": 1.0, "connection_error": 1.0, "rate_limited": 0.5}.get(outcome, 1.0)
    with _ACCOUNT_PENALTY_LOCK:
        score, _ = _ACCOUNT_PENALTIES.get(account_id, (0.0, 0.0))
        _ACCOUNT_PENALTIES[account_id] = (score + weight, time.monotonic() + cooldown)


def clear_account_penalties() -> None:
    """Clear process-local account feedback; intended for lifecycle/tests."""
    with _ACCOUNT_PENALTY_LOCK:
        _ACCOUNT_PENALTIES.clear()


def next_permission_threshold(required_permission: int | None, current_min: int | None = None) -> int:
    """返回能访问该帖子的最低 permission_level.

    required_permission 是论坛返回的「阅读权限高于 X」中的 X,
    拥有 X 权限的号即可访问. 若算出的值不高于当前已尝试的阈值,
    则强制 +1 以避免原地循环. 无法提取权限时退化为 current_min + 1.
    """
    if required_permission is not None:
        threshold = required_permission
        if current_min is not None and threshold <= current_min:
            threshold = current_min + 1
        return threshold
    return (current_min or 0) + 1


def _identity_from_config(config: AccountConfig) -> AccountIdentity:
    return AccountIdentity(
        account_id=config.account_id,
        username=config.username,
        password=config.password,
        cookie_file=str(config.cookie_file),
        enabled=config.enabled,
        weight=config.weight,
        permission_level=config.permission_level,
        request_interval_seconds=config.request_interval_seconds,
        request_interval_jitter_seconds=config.request_interval_jitter_seconds,
        max_concurrent_leases=config.max_concurrent_leases,
        login_mode=config.login_mode,
    )


def _default_identity_from_settings(settings: Settings) -> AccountIdentity:
    cookie_file = Path(settings.cookie_file)
    if not cookie_file.exists():
        fallback = Path(settings.data_dir) / "cookies.txt"
        if fallback.exists():
            cookie_file = fallback
    return AccountIdentity(
        account_id="default",
        username=settings.login_username,
        password=settings.login_password,
        cookie_file=str(cookie_file),
        enabled=True,
        weight=1,
        permission_level=0,
        request_interval_seconds=settings.request_interval_seconds,
        request_interval_jitter_seconds=settings.request_interval_jitter_seconds,
        max_concurrent_leases=5,
        login_mode="refresh_on_login_required",
    )


class AccountPool:
    def __init__(self, identities: tuple[AccountIdentity, ...]):
        self._identities = identities
        self._lock = Lock()
        self._inflight = {identity.account_id: 0 for identity in identities}

    def acquire(
        self,
        *,
        account_id: str | None = None,
        min_permission: int | None = None,
        prefer_high_permission: bool = False,
    ) -> AccountIdentity:
        with self._lock:
            available = [
                identity
                for identity in self._identities
                if identity.enabled
                and (account_id is None or identity.account_id == account_id)
                and (min_permission is None or identity.permission_level >= min_permission)
            ]
            if not available:
                configured = ",".join(f"{identity.account_id}:{identity.permission_level}" for identity in self._identities if identity.enabled)
                LOG.warning(
                    "No Yamibo account matched min_permission=%s prefer_high_permission=%s configured=[%s]",
                    min_permission,
                    prefer_high_permission,
                    configured,
                )
                if min_permission is None:
                    raise ValueError("no enabled account identities configured")
                raise ValueError(f"no enabled account identities configured for permission >= {min_permission}")

            # A recent remote block should move the next normal borrow to a
            # different account when possible. Explicit account selection is
            # an operator-level request and remains exact.
            if account_id is None:
                now = time.monotonic()
                with _ACCOUNT_PENALTY_LOCK:
                    expired = [key for key, (_, until) in _ACCOUNT_PENALTIES.items() if until <= now]
                    for key in expired:
                        _ACCOUNT_PENALTIES.pop(key, None)
                    active_penalties = {
                        key: value for key, value in _ACCOUNT_PENALTIES.items() if value[1] > now
                    }
                preferred = [identity for identity in available if identity.account_id not in active_penalties]
                if preferred:
                    available = preferred

            def _score(identity: AccountIdentity) -> tuple[float, int, str]:
                load = self._inflight[identity.account_id] / max(identity.weight, 1)
                if prefer_high_permission:
                    return (-float(identity.permission_level), load, identity.account_id)
                return (float(identity.permission_level), load, identity.account_id)

            available.sort(key=_score)

            for identity in available:
                if self._inflight[identity.account_id] < identity.max_concurrent_leases:
                    self._inflight[identity.account_id] += 1
                    return identity

            identity = min(available, key=_score)
            self._inflight[identity.account_id] += 1
            return identity

    def release(self, identity: AccountIdentity) -> None:
        with self._lock:
            current = self._inflight.get(identity.account_id, 0)
            self._inflight[identity.account_id] = max(current - 1, 0)


_POOL_BY_KEY: dict[tuple[str, tuple[str, ...]], AccountPool] = {}
_POOL_REGISTRY_LOCK = Lock()


def get_account_pool(settings: Settings) -> AccountPool:
    raw_pool = getattr(settings, "account_pool", ())
    if not isinstance(raw_pool, (list, tuple)):
        raw_pool = ()
    configured = tuple(_identity_from_config(item) for item in raw_pool if item.enabled)
    identities = configured or (_default_identity_from_settings(settings),)
    key = (
        str(getattr(settings, "db_path", "default")),
        tuple(
            (
                identity.account_id,
                identity.cookie_file,
                identity.enabled,
                identity.weight,
                identity.permission_level,
                identity.request_interval_seconds,
                identity.request_interval_jitter_seconds,
                identity.max_concurrent_leases,
                identity.login_mode,
            )
            for identity in identities
        ),
    )
    with _POOL_REGISTRY_LOCK:
        pool = _POOL_BY_KEY.get(key)
        if pool is None:
            pool = AccountPool(identities)
            _POOL_BY_KEY[key] = pool
        return pool


def has_configured_account_pool(settings: Settings) -> bool:
    raw_pool = getattr(settings, "account_pool", ())
    if not isinstance(raw_pool, (list, tuple)):
        return False
    return any(item.enabled for item in raw_pool)


def get_account_identities(settings: Settings) -> tuple[AccountIdentity, ...]:
    """Return every enabled identity, preserving the configured account boundary."""
    raw_pool = getattr(settings, "account_pool", ())
    if isinstance(raw_pool, (list, tuple)):
        configured = tuple(_identity_from_config(item) for item in raw_pool if item.enabled)
        if configured:
            return configured
    return (_default_identity_from_settings(settings),)


def _refresh_cookie_if_stale(identity: AccountIdentity, settings: Settings) -> None:
    """Delete cookie file if older than the configured refresh interval, forcing re-login."""
    interval_hours = getattr(settings, "cookie_refresh_interval_hours", 12.0)
    if interval_hours <= 0:
        return
    if not identity.username or not identity.password:
        return  # can't login without credentials

    cookie_path = Path(identity.cookie_file)
    with _REFRESH_LOCK:
        last = _REFRESH_REGISTRY.get(identity.cookie_file, 0.0)
        now = time.monotonic()
        if now - last < interval_hours * 3600:
            return
        _REFRESH_REGISTRY[identity.cookie_file] = now

    if cookie_path.exists():
        LOG.info(
            "cookie_refresh: deleting stale cookie for account_id=%s age_hours=%.1f interval_hours=%.0f",
            identity.account_id,
            (now - last) / 3600,
            interval_hours,
        )
        cookie_path.unlink()


@contextmanager
def borrow_yamibo_client(
    settings: Settings,
    *,
    account_id: str | None = None,
    cookie_file: str | None = None,
    min_permission: int | None = None,
    prefer_high_permission: bool = False,
    proxy_url: str | None = None,
) -> Iterator[tuple[AccountIdentity, YamiboClient]]:
    if cookie_file is not None:
        identity = AccountIdentity(
            account_id="explicit_cookie_file",
            username=settings.login_username,
            password=settings.login_password,
            cookie_file=cookie_file,
            enabled=True,
            weight=1,
            permission_level=0,
            request_interval_seconds=settings.request_interval_seconds,
            request_interval_jitter_seconds=settings.request_interval_jitter_seconds,
            max_concurrent_leases=5,
            login_mode="refresh_on_login_required",
        )
        _refresh_cookie_if_stale(identity, settings)
        yield identity, YamiboClient(
            timeout=getattr(settings, "request_timeout_seconds", 15.0),
            cookie_file=identity.cookie_file,
            persist_cookies=True,
            use_system_proxy=settings.use_system_proxy,
            proxy_url=proxy_url,
            login_username=identity.username,
            login_password=identity.password,
            request_interval=identity.request_interval_seconds,
            request_interval_jitter=identity.request_interval_jitter_seconds,
        )
        return

    pool = get_account_pool(settings)
    identity = pool.acquire(
        account_id=account_id,
        min_permission=min_permission,
        prefer_high_permission=prefer_high_permission,
    )
    _refresh_cookie_if_stale(identity, settings)
    if min_permission is not None or prefer_high_permission:
        LOG.info(
            "Borrowed Yamibo account account_id=%s permission_level=%s min_permission=%s prefer_high_permission=%s",
            identity.account_id,
            identity.permission_level,
            min_permission,
            prefer_high_permission,
        )
    try:
        client = YamiboClient(
            timeout=getattr(settings, "request_timeout_seconds", 15.0),
            cookie_file=identity.cookie_file,
            persist_cookies=True,
            use_system_proxy=settings.use_system_proxy,
            proxy_url=proxy_url,
            login_username=identity.username,
            login_password=identity.password,
            request_interval=identity.request_interval_seconds,
            request_interval_jitter=identity.request_interval_jitter_seconds,
        )
        yield identity, client
    finally:
        pool.release(identity)
