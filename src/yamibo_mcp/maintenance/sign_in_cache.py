from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from yamibo_mcp.storage.atomic import atomic_write_text

SIGN_IN_CACHE_REFRESH_SECONDS = 6 * 60 * 60
SIGN_IN_CACHE_RELATIVE_PATH = Path("cache/sign_in_stats.json")
_CACHE_LOCK = threading.Lock()
_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def sign_in_cache_path(settings) -> Path:
    return settings.data_dir / SIGN_IN_CACHE_RELATIVE_PATH


def read_sign_in_cache(settings) -> dict[str, dict[str, Any]]:
    path = sign_in_cache_path(settings)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    accounts = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(accounts, dict):
        return {}
    return {str(account_id): value for account_id, value in accounts.items() if isinstance(value, dict)}


def is_fresh(entry: dict[str, Any], *, now: datetime | None = None) -> bool:
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, str):
        return False
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if fetched.astimezone(_LOCAL_TIMEZONE).date() != current.astimezone(_LOCAL_TIMEZONE).date():
        return False
    return 0 <= (current - fetched).total_seconds() < SIGN_IN_CACHE_REFRESH_SECONDS


def write_sign_in_cache(settings, accounts: dict[str, dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "accounts": accounts,
    }
    with _CACHE_LOCK:
        atomic_write_text(
            sign_in_cache_path(settings),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )


def update_sign_in_cache_account(
    settings,
    account_id: str,
    profile: dict[str, Any],
    *,
    fetched_at: str | None = None,
    refresh_timestamp: bool = True,
) -> None:
    accounts = read_sign_in_cache(settings)
    previous = accounts.get(account_id, {})
    data = previous.get("data") if isinstance(previous.get("data"), dict) else {}
    data = {**data, **profile}
    timestamp = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not refresh_timestamp:
        timestamp = str(previous.get("fetched_at") or "1970-01-01T00:00:00+00:00")
    accounts[account_id] = {
        "fetched_at": timestamp,
        "data": data,
    }
    write_sign_in_cache(settings, accounts)
