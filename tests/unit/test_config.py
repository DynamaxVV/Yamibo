from __future__ import annotations

import json
import pytest

from yamibo_mcp.config import load_settings, refresh_llm_settings


def test_llm_connection_fields_refresh_without_reloading_other_settings(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    for name in ("YAMIBO_LLM_BASE_URL", "YAMIBO_LLM_API_KEY", "YAMIBO_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    original = load_settings()
    config_path.write_text(json.dumps({"llm": {"base_url": "http://localhost:8317/v1", "api_key": "new-key", "model": "new-model"}, "chat": {"timeout": 1}}))

    refreshed = refresh_llm_settings(original)
    assert (refreshed.llm_base_url, refreshed.llm_api_key, refreshed.llm_model) == ("http://localhost:8317/v1", "new-key", "new-model")
    assert refreshed.chat_timeout == original.chat_timeout
    assert refreshed.rag_base_url == refreshed.llm_base_url
    assert refreshed.rag_api_key == refreshed.llm_api_key
    monkeypatch.setenv("YAMIBO_LLM_MODEL", "locked-model")
    assert refresh_llm_settings(original).llm_model == "locked-model"


def test_chat_defaults_to_embedded_without_model_credentials(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(tmp_path / "data"))
    for name in ("YAMIBO_CHAT_BACKEND", "YAMIBO_LLM_BASE_URL", "YAMIBO_LLM_API_KEY", "YAMIBO_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.chat_backend == "embedded"
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_model == "gpt-4.1-mini"
    assert settings.llm_api_key is None


def test_chat_can_explicitly_select_legacy_hermes_backend(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text(json.dumps({"chat": {"backend": "hermes"}}), encoding="utf-8")
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("YAMIBO_CHAT_BACKEND", raising=False)

    assert load_settings().chat_backend == "hermes"


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
                            "permission_level": 3,
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
    assert identity.permission_level == 3
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


def test_load_settings_reads_rag_configuration(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "rag": {
                    "enabled": True,
                    "base_url": "https://embeddings.example.com/v1",
                    "api_key": "rag-key",
                    "embedding_provider": "openai",
                    "embedding_model": "text-embedding-3-small",
                    "embedding_dimensions": 256,
                    "chunker_version": "rag-chunker-v2",
                    "min_chunk_chars": 42,
                    "max_chunk_chars": 777,
                    "hybrid_fts_candidates": 12,
                    "hybrid_vector_candidates": 34,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.rag_enabled is True
    assert settings.rag_base_url == "https://embeddings.example.com/v1"
    assert settings.rag_api_key == "rag-key"
    assert settings.rag_embedding_model == "text-embedding-3-small"
    assert settings.rag_embedding_dimensions == 256
    assert settings.rag_chunker_version == "anime-chunker-1.2"
    assert settings.rag_min_chunk_chars == 42
    assert settings.rag_max_chunk_chars == 777
    assert settings.rag_hybrid_fts_candidates == 12
    assert settings.rag_hybrid_vector_candidates == 34


def test_load_settings_reads_archive_thread_max_pages(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "yamibo": {
                    "archive_thread_max_pages": 12,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.archive_thread_max_pages == 12


def test_load_settings_reads_worker_parallelism(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "worker": {
                    "parallelism": 3,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.worker_parallelism == 3


def test_load_settings_reads_persistent_jobs_enabled(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps({"worker": {"jobs_enabled": False}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.jobs_enabled is False


def test_load_settings_reads_database_configuration(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "database": {
                    "backend": "postgres",
                    "url": "postgresql://yamibo:secret@db.example.com:5432/yamibo",
                    "pool_min": 2,
                    "pool_max": 8,
                    "pool_timeout": 12.5,
                    "connect_timeout": 4.5,
                    "schema": "yamibo_test",
                    "ssl_mode": "require",
                    "ssl_root_cert": "certs/ca.pem",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))
    monkeypatch.delenv("YAMIBO_DB_BACKEND", raising=False)
    monkeypatch.delenv("YAMIBO_DB_URL", raising=False)
    monkeypatch.setenv("YAMIBO_DB_POOL_MAX", "10")

    settings = load_settings()

    assert settings.db_backend == "postgres"
    assert settings.db_url == "postgresql://yamibo:secret@db.example.com:5432/yamibo"
    assert settings.db_pool_min == 2
    assert settings.db_pool_max == 10
    assert settings.db_pool_timeout == 12.5
    assert settings.db_connect_timeout == 4.5
    assert settings.db_schema == "yamibo_test"
    assert settings.db_ssl_mode == "require"
    assert settings.db_ssl_root_cert == config_path.parent / "certs/ca.pem"


def test_load_settings_rejects_invalid_database_backend(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(json.dumps({"database": {"backend": "oracle"}}), encoding="utf-8")
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))
    monkeypatch.delenv("YAMIBO_DB_BACKEND", raising=False)
    monkeypatch.delenv("YAMIBO_DB_URL", raising=False)

    with pytest.raises(ValueError, match="unsupported database backend"):
        load_settings()


def test_load_settings_proxy_pool_default_disabled(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.proxy_pool.enabled is False


def test_load_settings_parses_proxy_pool(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "yamibo": {
                    "proxy_pool": {
                        "enabled": True,
                        "controller_url": "http://127.0.0.1:9090",
                        "secret": "test-secret",
                        "proxy_url": "http://127.0.0.1:7890",
                        "selector_group": "yamibo",
                        "test_url": "https://www.gstatic.com/generate_204",
                        "test_timeout_ms": 3000,
                        "failure_policy": "fail_open",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.proxy_pool.enabled is True
    assert settings.proxy_pool.controller_url == "http://127.0.0.1:9090"
    assert settings.proxy_pool.secret == "test-secret"
    assert settings.proxy_pool.proxy_url == "http://127.0.0.1:7890"
    assert settings.proxy_pool.selector_group == "yamibo"
    assert settings.proxy_pool.test_url == "https://www.gstatic.com/generate_204"
    assert settings.proxy_pool.test_timeout_ms == 3000
    assert settings.proxy_pool.failure_policy == "fail_open"


def test_load_settings_rag_endpoint_falls_back_to_llm(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    data_dir = tmp_path / "data"
    config_path.write_text(
        json.dumps(
            {
                "llm": {
                    "base_url": "https://chat.example.com/v1",
                    "api_key": "shared-key",
                    "model": "gpt-4.1-mini",
                },
                "rag": {
                    "enabled": True,
                    "embedding_model": "text-embedding-3-small",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.llm_base_url == "https://chat.example.com/v1"
    assert settings.llm_api_key == "shared-key"
    assert settings.rag_base_url == "https://chat.example.com/v1"
    assert settings.rag_api_key == "shared-key"


def test_legacy_title_parse_environment_overrides_file_mode(monkeypatch, tmp_path):
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text(json.dumps({"title": {"parse_mode": "always"}}), encoding="utf-8")
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("YAMIBO_TITLE_PARSE_MODE", raising=False)
    monkeypatch.setenv("YAMIBO_TITLE_PARSE_USE_LLM", "false")

    settings = load_settings()

    assert settings.title_parse_mode == "rules_only"
