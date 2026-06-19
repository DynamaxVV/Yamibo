from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def utc_after_iso(seconds: int) -> str:
    return (utc_now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")
