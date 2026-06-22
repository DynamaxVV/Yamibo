from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from yamibo_mcp.config import AccountConfig
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, get_account_pool


def _settings(tmp_path: Path, *, account_pool=()):
    cookie_file = tmp_path / "default.cookie"
    return SimpleNamespace(
        db_path=tmp_path / "test.db",
        cookie_file=cookie_file,
        login_username="default_user",
        login_password="default_pass",
        use_system_proxy=False,
        request_interval_seconds=0.0,
        request_interval_jitter_seconds=0.0,
        account_pool=account_pool,
    )


def test_borrow_yamibo_client_uses_default_identity_when_pool_missing(tmp_path):
    settings = _settings(tmp_path)

    with borrow_yamibo_client(settings) as (identity, client):
        assert identity.account_id == "default"
        assert client.login_username == "default_user"
        assert client.login_password == "default_pass"
        assert client.persist_cookies is True
        assert client.cookie_file == settings.cookie_file


def test_account_pool_rotates_between_configured_identities(tmp_path):
    settings = _settings(
        tmp_path,
        account_pool=(
            AccountConfig(
                account_id="a",
                username="user_a",
                password="pass_a",
                cookie_file=tmp_path / "a.cookie",
                enabled=True,
                weight=1,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
            AccountConfig(
                account_id="b",
                username="user_b",
                password="pass_b",
                cookie_file=tmp_path / "b.cookie",
                enabled=True,
                weight=1,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
        ),
    )
    pool = get_account_pool(settings)

    first = pool.acquire()
    second = pool.acquire()
    pool.release(first)
    pool.release(second)

    assert first.account_id == "a"
    assert second.account_id == "b"


def test_borrow_yamibo_client_uses_explicit_cookie_override(tmp_path):
    settings = _settings(tmp_path)
    explicit_cookie = tmp_path / "manual.cookie"

    with borrow_yamibo_client(settings, cookie_file=str(explicit_cookie)) as (identity, client):
        assert identity.account_id == "explicit_cookie_file"
        assert client.cookie_file == explicit_cookie
