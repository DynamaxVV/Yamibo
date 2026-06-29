from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from pathlib import Path

from yamibo_mcp.config import AccountConfig, Settings
from yamibo_mcp.yamibo.client import YamiboClient

LOG = logging.getLogger(__name__)


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


def next_permission_threshold(required_permission: int | None) -> int:
    # ponytail: 只提升到“高于当前门槛”的最小值，让账号池自然落到下一档可用账号。
    return (required_permission or 0) + 1


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

    def acquire(self, *, min_permission: int | None = None, prefer_high_permission: bool = False) -> AccountIdentity:
        with self._lock:
            available = [
                identity
                for identity in self._identities
                if identity.enabled and (min_permission is None or identity.permission_level >= min_permission)
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


@contextmanager
def borrow_yamibo_client(
    settings: Settings,
    *,
    cookie_file: str | None = None,
    min_permission: int | None = None,
    prefer_high_permission: bool = False,
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
    identity = pool.acquire(min_permission=min_permission, prefer_high_permission=prefer_high_permission)
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
            login_username=identity.username,
            login_password=identity.password,
            request_interval=identity.request_interval_seconds,
            request_interval_jitter=identity.request_interval_jitter_seconds,
        )
        yield identity, client
    finally:
        pool.release(identity)
