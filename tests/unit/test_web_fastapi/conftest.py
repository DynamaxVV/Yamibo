from __future__ import annotations

import pytest
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _TestSettings:
    project_root: Path
    data_dir: Path
    db_path: Path
    db_backend: str = "sqlite"
    db_url: str | None = None
    web_host: str = "127.0.0.1"
    web_port: int = 18765
    jobs_enabled: bool = True
    config_path: Path | None = None
    db_pool_min: int = 1
    db_pool_max: int = 2
    db_pool_timeout: float = 5.0
    db_connect_timeout: float = 5.0
    db_schema: str = "public"
    db_ssl_mode: str = "prefer"
    db_ssl_root_cert: Path | None = None
    title_hints_path: Path | None = None
    worker_id: str | None = None
    worker_poll_seconds: float = 1.0
    worker_parallelism: int = 1
    worker_lease_seconds: int = 30
    worker_heartbeat_seconds: int = 10
    cookie_file: Path | None = None
    login_username: str | None = None
    login_password: str | None = None
    use_system_proxy: bool = False
    image_download_timeout_seconds: float = 30.0
    image_download_retries: int = 1
    archive_thread_max_pages: int = 10
    novel_author_only_max_pages: int = 10
    novel_author_only_page_delay_seconds: float = 0.1
    export_dir: Path | None = None
    novel_txt_export_dir: Path | None = None
    export_default_strategy: str = "cache_only"
    export_stale_after_hours: int = 24
    novel_txt_include_filtered_notes: bool = False
    novel_txt_debug_markers: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"
    hermes_endpoint: str = "http://127.0.0.1:8642"
    hermes_host: str = "127.0.0.1"
    hermes_port: int = 8642
    hermes_api_key: str | None = None
    hermes_model: str = "hermes-agent"
    hermes_stream: bool = False
    rag_enabled: bool = False
    rag_base_url: str = "https://api.openai.com/v1"
    rag_api_key: str | None = None
    rag_debug_indexing: bool = False
    rag_embedding_provider: str = "openai"
    rag_embedding_model: str = "text-embedding-3-small"
    rag_embedding_dimensions: int = 512
    rag_chunker_version: str = "rag-chunker-v1"
    rag_min_chunk_chars: int = 20
    rag_max_chunk_chars: int = 900
    rag_hybrid_fts_candidates: int = 50
    rag_hybrid_vector_candidates: int = 50
    title_parse_use_llm: bool = False
    common_scanlation_groups: list = ()
    common_authors: list = ()
    backup_dir: Path | None = None
    backup_keep_count: int = 5
    cleanup_staging_older_than_hours: int = 48
    request_timeout_seconds: float = 10.0
    request_interval_seconds: float = 0.0
    request_interval_jitter_seconds: float = 0.0
    account_pool: tuple = ()
    proxy_pool: object = None
    cookie_refresh_interval_hours: float = 12.0


@pytest.fixture
def test_settings(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = tmp_path / "test.db"
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    config_path = tmp_path / "yamibo.local.json"
    config_path.write_text("{}")
    title_hints_path = tmp_path / "title_hints.json"
    title_hints_path.write_text('{"scanlation_groups": [], "authors": []}')
    return _TestSettings(
        project_root=tmp_path,
        data_dir=data_dir,
        db_path=db_path,
        export_dir=export_dir,
        backup_dir=backup_dir,
        config_path=config_path,
        title_hints_path=title_hints_path,
        cookie_file=tmp_path / ".cookie",
    )


@pytest.fixture
def app(test_settings):
    from yamibo_mcp.web_fastapi.app import create_app
    return create_app(test_settings)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)
