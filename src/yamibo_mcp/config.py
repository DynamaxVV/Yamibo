from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AccountConfig:
    account_id: str
    username: str | None
    password: str | None
    cookie_file: Path
    enabled: bool
    weight: int
    request_interval_seconds: float
    request_interval_jitter_seconds: float
    max_concurrent_leases: int
    login_mode: str


@dataclass(frozen=True)
class Settings:
    project_root: Path
    config_path: Path
    data_dir: Path
    db_path: Path
    title_hints_path: Path
    web_host: str
    web_port: int
    worker_id: str | None
    worker_poll_seconds: float
    worker_parallelism: int
    worker_lease_seconds: int
    worker_heartbeat_seconds: int
    cookie_file: Path
    login_username: str | None
    login_password: str | None
    use_system_proxy: bool
    image_download_timeout_seconds: float
    image_download_retries: int
    archive_thread_max_pages: int
    novel_author_only_max_pages: int
    novel_author_only_page_delay_seconds: float
    export_dir: Path
    novel_txt_export_dir: Path
    export_default_strategy: str
    export_stale_after_hours: int
    novel_txt_include_filtered_notes: bool
    novel_txt_debug_markers: bool
    llm_base_url: str
    llm_api_key: str | None
    llm_model: str
    rag_enabled: bool
    rag_base_url: str
    rag_api_key: str | None
    rag_debug_indexing: bool
    rag_embedding_provider: str
    rag_embedding_model: str
    rag_embedding_dimensions: int
    rag_chunker_version: str
    rag_min_chunk_chars: int
    rag_max_chunk_chars: int
    rag_hybrid_fts_candidates: int
    rag_hybrid_vector_candidates: int
    title_parse_use_llm: bool
    common_scanlation_groups: list[str]
    common_authors: list[str]
    backup_dir: Path
    backup_keep_count: int
    cleanup_staging_older_than_hours: int
    request_timeout_seconds: float
    request_interval_seconds: float
    request_interval_jitter_seconds: float
    account_pool: tuple[AccountConfig, ...]


def _read_local_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cfg_value(config: dict[str, object], section: str, key: str, default):
    section_value = config.get(section, {})
    if isinstance(section_value, dict) and key in section_value:
        return section_value[key]
    return default


def _cfg_list(config: dict[str, object], section: str, key: str, default: list[str] | None = None) -> list[str]:
    value = _cfg_value(config, section, key, default or [])
    if not isinstance(value, list):
        return list(default or [])
    items = [str(item).strip() for item in value if str(item).strip()]
    return items


def _cfg_account_pool(config: dict[str, object], *, config_dir: Path, data_dir: Path) -> tuple[AccountConfig, ...]:
    raw_pool = _cfg_value(config, "yamibo", "account_pool", [])
    if not isinstance(raw_pool, list):
        return ()

    identities: list[AccountConfig] = []
    seen_account_ids: set[str] = set()
    seen_cookie_files: set[Path] = set()
    cookie_root = data_dir / "cookies"
    for index, item in enumerate(raw_pool, start=1):
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("account_id") or f"account_{index}").strip()
        username = str(item.get("username")).strip() if item.get("username") not in {None, ""} else None
        password = str(item.get("password")).strip() if item.get("password") not in {None, ""} else None
        cookie_file_value = item.get("cookie_file")
        if cookie_file_value in {None, ""}:
            cookie_file = cookie_root / f"{account_id}.cookie"
        else:
            cookie_file = Path(str(cookie_file_value)).expanduser()
            if not cookie_file.is_absolute():
                cookie_file = (config_dir / cookie_file).resolve()
        if account_id in seen_account_ids:
            raise ValueError(f"duplicate yamibo.account_pool account_id: {account_id}")
        if cookie_file in seen_cookie_files:
            raise ValueError(f"duplicate yamibo.account_pool cookie_file: {cookie_file}")
        seen_account_ids.add(account_id)
        seen_cookie_files.add(cookie_file)
        enabled = str(item.get("enabled", True)).lower() in {"1", "true", "yes", "on"}
        weight = max(int(item.get("weight", 1) or 1), 1)
        request_interval_seconds = float(item.get("request_interval_seconds", 1.0) or 0.0)
        request_interval_jitter_seconds = float(item.get("request_interval_jitter_seconds", 0.5) or 0.0)
        max_concurrent_leases = max(int(item.get("max_concurrent_leases", 5) or 5), 1)
        login_mode = str(item.get("login_mode") or "refresh_on_login_required").strip() or "refresh_on_login_required"
        identities.append(
            AccountConfig(
                account_id=account_id,
                username=username,
                password=password,
                cookie_file=cookie_file,
                enabled=enabled,
                weight=weight,
                request_interval_seconds=request_interval_seconds,
                request_interval_jitter_seconds=request_interval_jitter_seconds,
                max_concurrent_leases=max_concurrent_leases,
                login_mode=login_mode,
            )
        )
    return tuple(identities)


def load_settings() -> Settings:
    root = _project_root()
    config_path = Path(os.environ.get("YAMIBO_CONFIG_PATH", root / "yamibo.local.json")).expanduser()
    config = _read_local_config(config_path)
    data_dir = Path(os.environ.get("YAMIBO_DATA_DIR", root / "data")).expanduser()
    db_path = Path(os.environ.get("YAMIBO_DB_PATH", data_dir / "forum.db")).expanduser()
    title_hints_path = Path(
        os.environ.get(
            "YAMIBO_TITLE_HINTS_PATH",
            str(_cfg_value(config, "title", "hints_path", str(data_dir / "title_hints.json"))),
        )
    ).expanduser()
    backup_dir = Path(
        os.environ.get(
            "YAMIBO_BACKUP_DIR",
            _cfg_value(config, "maintenance", "backup_dir", str(data_dir / "backups")),
        )
    ).expanduser()
    cookie_file = Path(
        os.environ.get(
            "YAMIBO_COOKIE_FILE",
            _cfg_value(config, "yamibo", "cookie_file", str(root / ".cookie")),
        )
    ).expanduser()
    export_dir_env = os.environ.get("YAMIBO_EXPORT_DIR")
    if export_dir_env:
        export_dir = Path(export_dir_env).expanduser()
    elif os.environ.get("YAMIBO_DATA_DIR"):
        export_dir = (data_dir / "exports").expanduser()
    else:
        export_dir = Path(_cfg_value(config, "export", "dir", str(data_dir / "exports"))).expanduser()
    novel_txt_export_dir = Path(
        os.environ.get(
            "YAMIBO_NOVEL_TXT_EXPORT_DIR",
            str(_cfg_value(config, "export", "novel_txt_dir", str(data_dir / "novel_exports"))),
        )
    ).expanduser()
    account_pool = _cfg_account_pool(config, config_dir=config_path.parent, data_dir=data_dir)
    llm_base_url = str(
        os.environ.get(
            "YAMIBO_LLM_BASE_URL",
            str(_cfg_value(config, "llm", "base_url", "https://api.openai.com/v1")),
        )
    )
    llm_api_key = os.environ.get("YAMIBO_LLM_API_KEY") or (
        None
        if _cfg_value(config, "llm", "api_key", None) in {None, ""}
        else str(_cfg_value(config, "llm", "api_key", None))
    )
    return Settings(
        project_root=root,
        config_path=config_path,
        data_dir=data_dir,
        db_path=db_path,
        title_hints_path=title_hints_path,
        web_host=os.environ.get("YAMIBO_WEB_HOST", str(_cfg_value(config, "web", "host", "0.0.0.0"))),
        web_port=int(os.environ.get("YAMIBO_WEB_PORT", str(_cfg_value(config, "web", "port", 8765)))),
        worker_id=os.environ.get("YAMIBO_WORKER_ID") or None,
        worker_poll_seconds=float(
            os.environ.get("YAMIBO_WORKER_POLL_SECONDS", str(_cfg_value(config, "worker", "poll_seconds", 2)))
        ),
        worker_parallelism=max(
            int(os.environ.get("YAMIBO_WORKER_PARALLELISM", str(_cfg_value(config, "worker", "parallelism", 2)))),
            1,
        ),
        worker_lease_seconds=int(
            os.environ.get("YAMIBO_WORKER_LEASE_SECONDS", str(_cfg_value(config, "worker", "lease_seconds", 60)))
        ),
        worker_heartbeat_seconds=int(
            os.environ.get("YAMIBO_WORKER_HEARTBEAT_SECONDS", str(_cfg_value(config, "worker", "heartbeat_seconds", 15)))
        ),
        cookie_file=cookie_file,
        login_username=os.environ.get("YAMIBO_LOGIN_USERNAME")
        or (
            None
            if _cfg_value(config, "login", "username", None) in {None, ""}
            else str(_cfg_value(config, "login", "username", None))
        ),
        login_password=os.environ.get("YAMIBO_LOGIN_PASSWORD")
        or (
            None
            if _cfg_value(config, "login", "password", None) in {None, ""}
            else str(_cfg_value(config, "login", "password", None))
        ),
        use_system_proxy=str(os.environ.get("YAMIBO_USE_SYSTEM_PROXY", str(_cfg_value(config, "yamibo", "use_system_proxy", False)))).lower()
        in {"1", "true", "yes", "on"},
        image_download_timeout_seconds=float(
            os.environ.get(
                "YAMIBO_IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
                str(_cfg_value(config, "yamibo", "image_download_timeout_seconds", 45)),
            )
        ),
        image_download_retries=int(
            os.environ.get(
                "YAMIBO_IMAGE_DOWNLOAD_RETRIES",
                str(_cfg_value(config, "yamibo", "image_download_retries", 2)),
            )
        ),
        archive_thread_max_pages=int(
            os.environ.get(
                "YAMIBO_ARCHIVE_THREAD_MAX_PAGES",
                str(_cfg_value(config, "yamibo", "archive_thread_max_pages", 50)),
            )
        ),
        novel_author_only_max_pages=int(
            os.environ.get(
                "YAMIBO_NOVEL_AUTHOR_ONLY_MAX_PAGES",
                str(_cfg_value(config, "yamibo", "novel_author_only_max_pages", 50)),
            )
        ),
        novel_author_only_page_delay_seconds=float(
            os.environ.get(
                "YAMIBO_NOVEL_AUTHOR_ONLY_PAGE_DELAY_SECONDS",
                str(_cfg_value(config, "yamibo", "novel_author_only_page_delay_seconds", 0.5)),
            )
        ),
        export_dir=export_dir,
        novel_txt_export_dir=novel_txt_export_dir,
        export_default_strategy=str(
            os.environ.get(
                "YAMIBO_EXPORT_DEFAULT_STRATEGY",
                str(_cfg_value(config, "export", "default_strategy", "cache_only")),
            )
        ),
        export_stale_after_hours=int(
            os.environ.get(
                "YAMIBO_EXPORT_STALE_AFTER_HOURS",
                str(_cfg_value(config, "export", "stale_after_hours", 24)),
            )
        ),
        novel_txt_include_filtered_notes=str(
            os.environ.get(
                "YAMIBO_NOVEL_TXT_INCLUDE_FILTERED_NOTES",
                str(_cfg_value(config, "export", "novel_txt_include_filtered_notes", False)),
            )
        ).lower()
        in {"1", "true", "yes", "on"},
        novel_txt_debug_markers=str(
            os.environ.get(
                "YAMIBO_NOVEL_TXT_DEBUG_MARKERS",
                str(_cfg_value(config, "export", "novel_txt_debug_markers", False)),
            )
        ).lower()
        in {"1", "true", "yes", "on"},
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=str(
            os.environ.get(
                "YAMIBO_LLM_MODEL",
                str(_cfg_value(config, "llm", "model", "gpt-4.1-mini")),
            )
        ),
        rag_enabled=str(
            os.environ.get(
                "YAMIBO_RAG_ENABLED",
                str(_cfg_value(config, "rag", "enabled", True)),
            )
        ).lower()
        in {"1", "true", "yes", "on"},
        rag_base_url=str(
            os.environ.get(
                "YAMIBO_RAG_BASE_URL",
                str(_cfg_value(config, "rag", "base_url", llm_base_url)),
            )
        ),
        rag_api_key=os.environ.get("YAMIBO_RAG_API_KEY")
        or (
            None
            if _cfg_value(config, "rag", "api_key", llm_api_key) in {None, ""}
            else str(_cfg_value(config, "rag", "api_key", llm_api_key))
        ),
        rag_debug_indexing=str(
            os.environ.get(
                "YAMIBO_RAG_DEBUG_INDEXING",
                str(_cfg_value(config, "rag", "debug_indexing", False)),
            )
        ).lower()
        in {"1", "true", "yes", "on"},
        rag_embedding_provider=str(
            os.environ.get(
                "YAMIBO_RAG_EMBEDDING_PROVIDER",
                str(_cfg_value(config, "rag", "embedding_provider", "openai")),
            )
        ),
        rag_embedding_model=str(
            os.environ.get(
                "YAMIBO_RAG_EMBEDDING_MODEL",
                str(_cfg_value(config, "rag", "embedding_model", "text-embedding-3-small")),
            )
        ),
        rag_embedding_dimensions=int(
            os.environ.get(
                "YAMIBO_RAG_EMBEDDING_DIMENSIONS",
                str(_cfg_value(config, "rag", "embedding_dimensions", 512)),
            )
        ),
        rag_chunker_version=str(
            os.environ.get(
                "YAMIBO_RAG_CHUNKER_VERSION",
                str(_cfg_value(config, "rag", "chunker_version", "rag-chunker-v1")),
            )
        ),
        rag_min_chunk_chars=int(
            os.environ.get(
                "YAMIBO_RAG_MIN_CHUNK_CHARS",
                str(_cfg_value(config, "rag", "min_chunk_chars", 20)),
            )
        ),
        rag_max_chunk_chars=int(
            os.environ.get(
                "YAMIBO_RAG_MAX_CHUNK_CHARS",
                str(_cfg_value(config, "rag", "max_chunk_chars", 900)),
            )
        ),
        rag_hybrid_fts_candidates=int(
            os.environ.get(
                "YAMIBO_RAG_HYBRID_FTS_CANDIDATES",
                str(_cfg_value(config, "rag", "hybrid_fts_candidates", 50)),
            )
        ),
        rag_hybrid_vector_candidates=int(
            os.environ.get(
                "YAMIBO_RAG_HYBRID_VECTOR_CANDIDATES",
                str(_cfg_value(config, "rag", "hybrid_vector_candidates", 50)),
            )
        ),
        title_parse_use_llm=str(
            os.environ.get(
                "YAMIBO_TITLE_PARSE_USE_LLM",
                str(_cfg_value(config, "title", "use_llm", True)),
            )
        ).lower()
        in {"1", "true", "yes", "on"},
        common_scanlation_groups=_cfg_list(config, "title", "common_scanlation_groups", []),
        common_authors=_cfg_list(config, "title", "common_authors", []),
        backup_dir=backup_dir,
        backup_keep_count=int(
            os.environ.get(
                "YAMIBO_BACKUP_KEEP_COUNT",
                str(_cfg_value(config, "maintenance", "backup_keep_count", 20)),
            )
        ),
        cleanup_staging_older_than_hours=int(
            os.environ.get(
                "YAMIBO_CLEANUP_STAGING_OLDER_THAN_HOURS",
                str(_cfg_value(config, "maintenance", "cleanup_staging_older_than_hours", 48)),
            )
        ),
        request_timeout_seconds=float(
            os.environ.get(
                "YAMIBO_REQUEST_TIMEOUT_SECONDS",
                str(_cfg_value(config, "yamibo", "request_timeout_seconds", 30)),
            )
        ),
        request_interval_seconds=float(
            os.environ.get(
                "YAMIBO_REQUEST_INTERVAL_SECONDS",
                str(_cfg_value(config, "yamibo", "request_interval_seconds", 1.0)),
            )
        ),
        request_interval_jitter_seconds=float(
            os.environ.get(
                "YAMIBO_REQUEST_INTERVAL_JITTER_SECONDS",
                str(_cfg_value(config, "yamibo", "request_interval_jitter_seconds", 0.5)),
            )
        ),
        account_pool=account_pool,
    )
