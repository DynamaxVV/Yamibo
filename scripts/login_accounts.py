#!/usr/bin/env python3
"""Log in configured Yamibo accounts and persist their cookies.

Usage:
    uv run python scripts/login_accounts.py
    uv run python scripts/login_accounts.py --account-id primary
    uv run python scripts/login_accounts.py --skip-existing

Credentials are read through ``load_settings()``.  Cookie files are written to
an owner-only temporary file and atomically replaced only after login succeeds.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yamibo_mcp.config import AccountConfig, Settings, load_settings
from yamibo_mcp.yamibo.client import YamiboClient


@dataclass(frozen=True)
class LoginTarget:
    account_id: str
    username: str | None
    password: str | None
    cookie_file: Path
    request_interval_seconds: float
    request_interval_jitter_seconds: float


def _targets(settings: Settings) -> tuple[LoginTarget, ...]:
    if settings.account_pool:
        return tuple(_from_account(account) for account in settings.account_pool if account.enabled)
    return (
        LoginTarget(
            account_id="default",
            username=settings.login_username,
            password=settings.login_password,
            cookie_file=settings.data_dir / "cookies" / "default.cookie",
            request_interval_seconds=settings.request_interval_seconds,
            request_interval_jitter_seconds=settings.request_interval_jitter_seconds,
        ),
    )


def _from_account(account: AccountConfig) -> LoginTarget:
    return LoginTarget(
        account_id=account.account_id,
        username=account.username,
        password=account.password,
        cookie_file=account.cookie_file,
        request_interval_seconds=account.request_interval_seconds,
        request_interval_jitter_seconds=account.request_interval_jitter_seconds,
    )


def _login_one(settings: Settings, target: LoginTarget, *, skip_existing: bool) -> None:
    destination = target.cookie_file.expanduser()
    if skip_existing and destination.exists() and destination.stat().st_size > 0:
        print(f"{target.account_id}: SKIP existing cookie file={destination}")
        return
    if not target.username or not target.password:
        raise ValueError("username or password is not configured")

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.account_id}.",
        suffix=".cookie.tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    os.chmod(temporary, 0o600)

    client: YamiboClient | None = None
    try:
        # Disable constructor bootstrap so the script can surface login errors
        # instead of the client's best-effort bootstrap warning.
        client = YamiboClient(
            timeout=settings.request_timeout_seconds,
            retries=2,
            cookie_file=str(temporary),
            persist_cookies=True,
            use_system_proxy=settings.use_system_proxy,
            login_username=None,
            login_password=None,
            request_interval=target.request_interval_seconds,
            request_interval_jitter=target.request_interval_jitter_seconds,
        )
        client.login_username = target.username
        client.login_password = target.password

        # _login implements the forum's current form flow:
        # GET login page -> extract form action/formhash -> POST the form.
        client._login(base_url="https://bbs.yamibo.com", referer="https://bbs.yamibo.com/")

        cookie_count = len(list(client._session.cookies.items()))
        if not temporary.exists() or temporary.stat().st_size == 0 or cookie_count == 0:
            raise RuntimeError("login produced no cookies")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        print(f"{target.account_id}: OK cookies={cookie_count} file={destination}")
    finally:
        if client is not None:
            client._session.close()
        temporary.unlink(missing_ok=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account-id",
        action="append",
        dest="account_ids",
        help="limit the run to this account ID; repeat the option for multiple accounts",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="do not refresh a non-empty cookie file",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    settings = load_settings()
    targets = _targets(settings)
    selected = set(args.account_ids or ())
    if selected:
        targets = tuple(target for target in targets if target.account_id in selected)
        configured = {target.account_id for target in _targets(settings)}
        unknown = sorted(selected - configured)
        if unknown:
            print(f"unknown account_id: {', '.join(unknown)}", file=sys.stderr)
            return 2
    if not targets:
        print("no enabled accounts configured", file=sys.stderr)
        return 2

    failures = 0
    for target in targets:
        try:
            _login_one(settings, target, skip_existing=args.skip_existing)
        except Exception as exc:
            failures += 1
            print(f"{target.account_id}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
