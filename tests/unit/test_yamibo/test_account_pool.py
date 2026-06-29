from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from yamibo_mcp.config import AccountConfig
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, get_account_pool, next_permission_threshold


def _settings(tmp_path: Path, *, account_pool=()):
    cookie_file = tmp_path / "default.cookie"
    return SimpleNamespace(
        db_path=tmp_path / "test.db",
        data_dir=tmp_path / "data",
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


def test_account_pool_prefers_low_permission_for_archive_work(tmp_path):
    settings = _settings(
        tmp_path,
        account_pool=(
            AccountConfig(
                account_id="low",
                username="user_low",
                password="pass_low",
                cookie_file=tmp_path / "low.cookie",
                enabled=True,
                weight=1,
                permission_level=1,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
            AccountConfig(
                account_id="high",
                username="user_high",
                password="pass_high",
                cookie_file=tmp_path / "high.cookie",
                enabled=True,
                weight=1,
                permission_level=9,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
        ),
    )

    with borrow_yamibo_client(settings) as (identity, _client):
        assert identity.account_id == "low"


def test_account_pool_prefers_high_permission_for_forum_query(tmp_path):
    settings = _settings(
        tmp_path,
        account_pool=(
            AccountConfig(
                account_id="low",
                username="user_low",
                password="pass_low",
                cookie_file=tmp_path / "low.cookie",
                enabled=True,
                weight=1,
                permission_level=1,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
            AccountConfig(
                account_id="high",
                username="user_high",
                password="pass_high",
                cookie_file=tmp_path / "high.cookie",
                enabled=True,
                weight=1,
                permission_level=9,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
        ),
    )

    with borrow_yamibo_client(settings, prefer_high_permission=True) as (identity, _client):
        assert identity.account_id == "high"


def test_account_pool_can_require_minimum_permission(tmp_path):
    settings = _settings(
        tmp_path,
        account_pool=(
            AccountConfig(
                account_id="low",
                username="user_low",
                password="pass_low",
                cookie_file=tmp_path / "low.cookie",
                enabled=True,
                weight=1,
                permission_level=1,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
            AccountConfig(
                account_id="high",
                username="user_high",
                password="pass_high",
                cookie_file=tmp_path / "high.cookie",
                enabled=True,
                weight=1,
                permission_level=9,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
        ),
    )

    with borrow_yamibo_client(settings, min_permission=5) as (identity, _client):
        assert identity.account_id == "high"


def test_borrow_yamibo_client_logs_selected_identity_for_retry(tmp_path, caplog):
    settings = _settings(
        tmp_path,
        account_pool=(
            AccountConfig(
                account_id="low",
                username="user_low",
                password="pass_low",
                cookie_file=tmp_path / "low.cookie",
                enabled=True,
                weight=1,
                permission_level=10,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
            AccountConfig(
                account_id="high",
                username="user_high",
                password="pass_high",
                cookie_file=tmp_path / "high.cookie",
                enabled=True,
                weight=1,
                permission_level=20,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
        ),
    )

    with caplog.at_level("INFO"):
        with borrow_yamibo_client(settings, min_permission=11) as (identity, _client):
            assert identity.account_id == "high"

    assert "Borrowed Yamibo account account_id=high permission_level=20 min_permission=11 prefer_high_permission=False" in caplog.text


def test_borrow_yamibo_client_logs_when_no_account_matches_permission(tmp_path, caplog):
    settings = _settings(
        tmp_path,
        account_pool=(
            AccountConfig(
                account_id="low",
                username="user_low",
                password="pass_low",
                cookie_file=tmp_path / "low.cookie",
                enabled=True,
                weight=1,
                permission_level=10,
                request_interval_seconds=0.0,
                request_interval_jitter_seconds=0.0,
                max_concurrent_leases=1,
                login_mode="refresh_on_login_required",
            ),
        ),
    )

    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match="permission >= 11"):
            with borrow_yamibo_client(settings, min_permission=11):
                pass

    assert "No Yamibo account matched min_permission=11 prefer_high_permission=False configured=[low:10]" in caplog.text


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
                permission_level=0,
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
                permission_level=0,
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


def test_next_permission_threshold_moves_to_next_tier():
    assert next_permission_threshold(None) == 1
    assert next_permission_threshold(0) == 1
    assert next_permission_threshold(10) == 11
