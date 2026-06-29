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
    assert settings.rag_chunker_version == "rag-chunker-v2"
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

    with pytest.raises(ValueError, match="unsupported database backend"):
        load_settings()


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
