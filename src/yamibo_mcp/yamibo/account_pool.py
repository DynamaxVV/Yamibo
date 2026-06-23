from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock

from yamibo_mcp.config import AccountConfig, Settings
from yamibo_mcp.yamibo.client import YamiboClient


@dataclass(frozen=True)
class AccountIdentity:
    account_id: str
    username: str | None
    password: str | None
    cookie_file: str
    enabled: bool
    weight: int
    request_interval_seconds: float
    request_interval_jitter_seconds: float
    max_concurrent_leases: int
    login_mode: str


def _identity_from_config(config: AccountConfig) -> AccountIdentity:
    return AccountIdentity(
        account_id=config.account_id,
        username=config.username,
        password=config.password,
        cookie_file=str(config.cookie_file),
        enabled=config.enabled,
        weight=config.weight,
        request_interval_seconds=config.request_interval_seconds,
        request_interval_jitter_seconds=config.request_interval_jitter_seconds,
        max_concurrent_leases=config.max_concurrent_leases,
        login_mode=config.login_mode,
    )


def _default_identity_from_settings(settings: Settings) -> AccountIdentity:
    return AccountIdentity(
        account_id="default",
        username=settings.login_username,
        password=settings.login_password,
        cookie_file=str(settings.cookie_file),
        enabled=True,
        weight=1,
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
        self._cursor = 0

    def acquire(self) -> AccountIdentity:
        with self._lock:
            available = [identity for identity in self._identities if identity.enabled]
            if not available:
                raise ValueError("no enabled account identities configured")

            size = len(available)
            for offset in range(size):
                identity = available[(self._cursor + offset) % size]
                if self._inflight[identity.account_id] < identity.max_concurrent_leases:
                    self._inflight[identity.account_id] += 1
                    self._cursor = (self._cursor + offset + 1) % size
                    return identity

            identity = min(available, key=lambda item: self._inflight[item.account_id] / max(item.weight, 1))
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
    key = (str(getattr(settings, "db_path", "default")), tuple(identity.account_id for identity in identities))
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


@contextmanager
def borrow_yamibo_client(
    settings: Settings,
    *,
    cookie_file: str | None = None,
) -> Iterator[tuple[AccountIdentity, YamiboClient]]:
    if cookie_file is not None:
        identity = AccountIdentity(
            account_id="explicit_cookie_file",
            username=settings.login_username,
            password=settings.login_password,
            cookie_file=cookie_file,
            enabled=True,
            weight=1,
            request_interval_seconds=settings.request_interval_seconds,
            request_interval_jitter_seconds=settings.request_interval_jitter_seconds,
            max_concurrent_leases=5,
            login_mode="refresh_on_login_required",
        )
        yield identity, YamiboClient(
            timeout=getattr(settings, "request_timeout_seconds", 15.0),
            cookie_file=identity.cookie_file,
            persist_cookies=True,
            use_system_proxy=settings.use_system_proxy,
            login_username=identity.username,
            login_password=identity.password,
            request_interval=identity.request_interval_seconds,
            request_interval_jitter=identity.request_interval_jitter_seconds,
        )
        return

    pool = get_account_pool(settings)
    identity = pool.acquire()
    try:
        client = YamiboClient(
            timeout=getattr(settings, "request_timeout_seconds", 15.0),
            cookie_file=identity.cookie_file,
            persist_cookies=True,
            use_system_proxy=settings.use_system_proxy,
            login_username=identity.username,
            login_password=identity.password,
            request_interval=identity.request_interval_seconds,
            request_interval_jitter=identity.request_interval_jitter_seconds,
        )
        yield identity, client
    finally:
        pool.release(identity)
