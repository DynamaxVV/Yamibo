from __future__ import annotations

import json
import pytest

from yamibo_mcp.config import load_settings


def test_load_settings_parses_account_pool(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "yamibo": {
                    "account_pool": [
                        {
                            "account_id": "primary",
                            "username": "user_a",
                            "password": "pass_a",
                            "cookie_file": "data/cookies/primary.cookie",
                            "enabled": True,
                            "weight": 2,
                            "request_interval_seconds": 0.2,
                            "request_interval_jitter_seconds": 0.1,
                            "max_concurrent_leases": 2,
                            "login_mode": "refresh_on_login_required",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert len(settings.account_pool) == 1
    identity = settings.account_pool[0]
    assert identity.account_id == "primary"
    assert identity.username == "user_a"
    assert identity.password == "pass_a"
    assert identity.cookie_file == tmp_path / "data" / "cookies" / "primary.cookie"
    assert identity.max_concurrent_leases == 2
    assert settings.request_timeout_seconds == 30.0


def test_load_settings_rejects_duplicate_account_ids(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "yamibo": {
                    "account_pool": [
                        {"account_id": "dup", "username": "user_a", "password": "pass_a", "cookie_file": "data/cookies/a.cookie"},
                        {"account_id": "dup", "username": "user_b", "password": "pass_b", "cookie_file": "data/cookies/b.cookie"},
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="duplicate yamibo.account_pool account_id"):
        load_settings()


def test_load_settings_rejects_duplicate_cookie_files(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "yamibo": {
                    "account_pool": [
                        {"account_id": "a", "username": "user_a", "password": "pass_a", "cookie_file": "data/cookies/shared.cookie"},
                        {"account_id": "b", "username": "user_b", "password": "pass_b", "cookie_file": "data/cookies/shared.cookie"},
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    with pytest.raises(ValueError, match="duplicate yamibo.account_pool cookie_file"):
        load_settings()
