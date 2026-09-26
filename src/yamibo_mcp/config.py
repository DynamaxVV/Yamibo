from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from yamibo_mcp.yamibo.proxy_pool import MihomoProxyPoolConfig

from yamibo_mcp.storage.atomic import atomic_write_text

RAG_CHUNKER_VERSION = "anime-chunker-1.2"

def _project_root() -> Path:
    env_root = os.environ.get("YAMIBO_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path.cwd().resolve()


@dataclass(frozen=True)
class AccountConfig:
    account_id: str
    username: str | None
    password: str | None
    cookie_file: Path
    enabled: bool
    weight: int
    permission_level: int
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
    db_backend: Literal["sqlite", "postgres"]
    db_url: str | None
    db_pool_min: int
    db_pool_max: int
    db_pool_timeout: float
    db_connect_timeout: float
    db_schema: str
    db_ssl_mode: str
    db_ssl_root_cert: Path | None
    title_hints_path: Path
    web_host: str
    web_port: int
    worker_id: str | None
    jobs_enabled: bool
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
    hermes_api_key: str | None
    hermes_model: str
    hermes_host: str
    hermes_port: int
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
    proxy_pool: MihomoProxyPoolConfig
    cookie_refresh_interval_hours: float
    image_backfill_enabled: bool
    image_backfill_dry_run: bool
    image_backfill_forum_id: int
    image_backfill_auto_interval_seconds: float
    image_backfill_daily_limit: int
    image_backfill_max_pages: int
    image_backfill_fixed_after: str | None
    chat_backend: str = "embedded"
    chat_timeout: int = 900
    chat_max_parallel: int = 2
    chat_access_token: str | None = None
    settings_access_token: str | None = None
    auto_signin_enabled: bool = True
    title_parse_mode: str | None = None


def _read_local_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_local_config(path: Path) -> dict[str, object]:
    return _read_local_config(path)


def write_local_config(path: Path, config: dict[str, object]) -> None:
    atomic_write_text(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")


def _cfg_value(config: dict[str, object], section: str, key: str, default):
    section_value = config.get(section, {})
    if isinstance(section_value, dict) and key in section_value:
        return section_value[key]
    return default


def refresh_llm_settings(settings: Settings) -> Settings:
    """Read only model connection fields from disk for a new request or Run."""
    config = read_local_config(settings.config_path)
    base_url = str(os.environ.get("YAMIBO_LLM_BASE_URL", _cfg_value(config, "llm", "base_url", "https://api.openai.com/v1")))
    key = os.environ.get("YAMIBO_LLM_API_KEY") or _cfg_value(config, "llm", "api_key", None)
    model = str(os.environ.get("YAMIBO_LLM_MODEL", _cfg_value(config, "llm", "model", "gpt-4.1-mini")))
    rag_base_url = settings.rag_base_url
    rag_key = settings.rag_api_key
    if not os.environ.get("YAMIBO_RAG_BASE_URL") and not _cfg_value(config, "rag", "base_url", None):
        rag_base_url = base_url
    if not os.environ.get("YAMIBO_RAG_API_KEY") and not _cfg_value(config, "rag", "api_key", None):
        rag_key = key
    return replace(settings, llm_base_url=base_url, llm_api_key=str(key) if key else None,
                   llm_model=model, rag_base_url=rag_base_url, rag_api_key=str(rag_key) if rag_key else None)


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
        permission_level = int(item.get("permission_level", 0) or 0)
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
                permission_level=permission_level,
                request_interval_seconds=request_interval_seconds,
                request_interval_jitter_seconds=request_interval_jitter_seconds,
                max_concurrent_leases=max_concurrent_leases,
                login_mode=login_mode,
            )
        )
    return tuple(identities)


def load_settings() -> Settings:
    root = _project_root()
    config_path = Path(os.environ.get("YAMIBO_CONFIG_PATH", root / "data" / "yamibo.local.json")).expanduser()
    config = _read_local_config(config_path)
    data_dir = Path(os.environ.get("YAMIBO_DATA_DIR", root / "data")).expanduser()
    db_path = Path(os.environ.get("YAMIBO_DB_PATH", data_dir / "forum.db")).expanduser()
    db_section_backend = str(_cfg_value(config, "database", "backend", "sqlite")).strip().lower()
    db_backend = str(os.environ.get("YAMIBO_DB_BACKEND", db_section_backend) or "sqlite").strip().lower()
    if db_backend not in {"sqlite", "postgres"}:
        raise ValueError(f"unsupported database backend: {db_backend}")
    db_url_value = os.environ.get("YAMIBO_DB_URL")
    if db_url_value in {None, ""}:
        db_url_config = _cfg_value(config, "database", "url", None)
        db_url = None if db_url_config in {None, ""} else str(db_url_config)
    else:
        db_url = db_url_value
    db_pool_min = max(int(os.environ.get("YAMIBO_DB_POOL_MIN", str(_cfg_value(config, "database", "pool_min", 5)))), 1)
    db_pool_max = max(int(os.environ.get("YAMIBO_DB_POOL_MAX", str(_cfg_value(config, "database", "pool_max", 20)))), db_pool_min)
    db_pool_timeout = float(
        os.environ.get("YAMIBO_DB_POOL_TIMEOUT", str(_cfg_value(config, "database", "pool_timeout", 30.0)))
    )
    db_connect_timeout = float(
        os.environ.get("YAMIBO_DB_CONNECT_TIMEOUT", str(_cfg_value(config, "database", "connect_timeout", 10.0)))
    )
    db_schema = str(os.environ.get("YAMIBO_DB_SCHEMA", str(_cfg_value(config, "database", "schema", "public")))).strip() or "public"
    db_ssl_mode = str(os.environ.get("YAMIBO_DB_SSL_MODE", str(_cfg_value(config, "database", "ssl_mode", "prefer")))).strip() or "prefer"
    db_ssl_root_cert_value = os.environ.get("YAMIBO_DB_SSL_ROOT_CERT")
    if db_ssl_root_cert_value in {None, ""}:
        db_ssl_root_cert_config = _cfg_value(config, "database", "ssl_root_cert", None)
        if db_ssl_root_cert_config in {None, ""}:
            db_ssl_root_cert = None
        else:
            db_ssl_root_cert = Path(str(db_ssl_root_cert_config)).expanduser()
            if not db_ssl_root_cert.is_absolute():
                db_ssl_root_cert = (config_path.parent / db_ssl_root_cert).resolve()
    else:
        db_ssl_root_cert = Path(db_ssl_root_cert_value).expanduser()
        if not db_ssl_root_cert.is_absolute():
            db_ssl_root_cert = (config_path.parent / db_ssl_root_cert).resolve()
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
    export_dir_config = _cfg_value(config, "export", "dir", None)
    if export_dir_env:
        export_dir = Path(export_dir_env).expanduser()
    elif export_dir_config not in {None, ""}:
        export_dir = Path(str(export_dir_config)).expanduser()
    else:
        export_dir = (data_dir / "exports").expanduser()
    novel_txt_export_dir = Path(
        os.environ.get(
            "YAMIBO_NOVEL_TXT_EXPORT_DIR",
            str(_cfg_value(config, "export", "novel_txt_dir", str(data_dir / "novel_exports"))),
        )
    ).expanduser()
    account_pool = _cfg_account_pool(config, config_dir=config_path.parent, data_dir=data_dir)

    proxy_pool_section = _cfg_value(config, "yamibo", "proxy_pool", None)
    from yamibo_mcp.yamibo.proxy_pool import MihomoProxyPoolConfig
    proxy_pool = MihomoProxyPoolConfig.from_config_section(proxy_pool_section if isinstance(proxy_pool_section, dict) else None)
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
    title_parse_mode = os.environ.get("YAMIBO_TITLE_PARSE_MODE")
    if not title_parse_mode and "YAMIBO_TITLE_PARSE_USE_LLM" in os.environ:
        title_parse_mode = (
            "always"
            if str(os.environ["YAMIBO_TITLE_PARSE_USE_LLM"]).lower() in {"1", "true", "yes", "on"}
            else "rules_only"
        )
    if not title_parse_mode:
        title_parse_mode = _cfg_value(config, "title", "parse_mode", None)
    return Settings(
        project_root=root,
        config_path=config_path,
        data_dir=data_dir,
        db_path=db_path,
        db_backend=db_backend,  # type: ignore[arg-type]
        db_url=db_url,
        db_pool_min=db_pool_min,
        db_pool_max=db_pool_max,
        db_pool_timeout=db_pool_timeout,
        db_connect_timeout=db_connect_timeout,
        db_schema=db_schema,
        db_ssl_mode=db_ssl_mode,
        db_ssl_root_cert=db_ssl_root_cert,
        title_hints_path=title_hints_path,
        web_host=os.environ.get("YAMIBO_WEB_HOST", str(_cfg_value(config, "web", "host", "0.0.0.0"))),
        web_port=int(os.environ.get("YAMIBO_WEB_PORT", str(_cfg_value(config, "web", "port", 8765)))),
        worker_id=os.environ.get("YAMIBO_WORKER_ID") or None,
        jobs_enabled=str(os.environ.get("YAMIBO_JOBS_ENABLED", str(_cfg_value(config, "worker", "jobs_enabled", True)))).lower()
        in {"1", "true", "yes", "on"},
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
        chat_backend=str(os.environ.get("YAMIBO_CHAT_BACKEND", _cfg_value(config, "chat", "backend", "embedded"))),
        chat_timeout=int(os.environ.get("YAMIBO_CHAT_TIMEOUT", _cfg_value(config, "chat", "timeout", 900))),
        chat_max_parallel=int(os.environ.get("YAMIBO_CHAT_MAX_PARALLEL", _cfg_value(config, "chat", "max_parallel", 2))),
        chat_access_token=os.environ.get("YAMIBO_CHAT_ACCESS_TOKEN") or _cfg_value(config, "chat", "access_token", None),
        settings_access_token=(os.environ.get("YAMIBO_SETTINGS_ACCESS_TOKEN") or os.environ.get("YAMIBO_CHAT_ACCESS_TOKEN")
                               or _cfg_value(config, "security", "access_token", None) or _cfg_value(config, "chat", "access_token", None)),
        auto_signin_enabled=str(os.environ.get("YAMIBO_AUTO_SIGNIN_ENABLED", _cfg_value(config, "worker", "auto_signin_enabled", True))).lower() in {"1", "true", "yes", "on"},
        title_parse_mode=title_parse_mode,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=str(
            os.environ.get(
                "YAMIBO_LLM_MODEL",
                str(_cfg_value(config, "llm", "model", "gpt-4.1-mini")),
            )
        ),
        hermes_api_key=os.environ.get("YAMIBO_HERMES_API_KEY") or (
            None if _cfg_value(config, "chat", "hermes_api_key", None) in {None, ""} else str(_cfg_value(config, "chat", "hermes_api_key", None))
        ),
        hermes_model=str(
            os.environ.get(
                "YAMIBO_HERMES_MODEL",
                str(_cfg_value(config, "chat", "hermes_model", "hermes-agent")),
            )
        ),
        hermes_host=str(
            os.environ.get(
                "YAMIBO_HERMES_HOST",
                str(_cfg_value(config, "chat", "hermes_host", "host.docker.internal")),
            )
        ),
        hermes_port=int(
            os.environ.get(
                "YAMIBO_HERMES_PORT",
                str(_cfg_value(config, "chat", "hermes_port", 8642)),
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
        rag_chunker_version=RAG_CHUNKER_VERSION,
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
        proxy_pool=proxy_pool,
        cookie_refresh_interval_hours=float(
            os.environ.get(
                "YAMIBO_COOKIE_REFRESH_INTERVAL_HOURS",
                str(_cfg_value(config, "yamibo", "cookie_refresh_interval_hours", 12)),
            )
        ),
        image_backfill_enabled=str(
            os.environ.get(
                "YAMIBO_IMAGE_BACKFILL_ENABLED",
                str(_cfg_value(config, "yamibo", "image_backfill_enabled", True)),
            )
        ).lower() in {"1", "true", "yes", "on"},
        image_backfill_dry_run=str(
            os.environ.get(
                "YAMIBO_IMAGE_BACKFILL_DRY_RUN",
                str(_cfg_value(config, "yamibo", "image_backfill_dry_run", True)),
            )
        ).lower() in {"1", "true", "yes", "on"},
        image_backfill_forum_id=int(
            os.environ.get(
                "YAMIBO_IMAGE_BACKFILL_FORUM_ID",
                str(_cfg_value(config, "yamibo", "image_backfill_forum_id", 5)),
            )
        ),
        image_backfill_auto_interval_seconds=float(
            os.environ.get(
                "YAMIBO_IMAGE_BACKFILL_AUTO_INTERVAL_SECONDS",
                str(_cfg_value(config, "yamibo", "image_backfill_auto_interval_seconds", 60.0)),
            )
        ),
        image_backfill_daily_limit=int(
            os.environ.get(
                "YAMIBO_IMAGE_BACKFILL_DAILY_LIMIT",
                str(_cfg_value(config, "yamibo", "image_backfill_daily_limit", 100)),
            )
        ),
        image_backfill_max_pages=int(
            os.environ.get(
                "YAMIBO_IMAGE_BACKFILL_MAX_PAGES",
                str(_cfg_value(config, "yamibo", "image_backfill_max_pages", 1)),
            )
        ),
        image_backfill_fixed_after=os.environ.get("YAMIBO_IMAGE_BACKFILL_FIXED_AFTER") or (
            None if _cfg_value(config, "yamibo", "image_backfill_fixed_after", None) in {None, ""} else str(_cfg_value(config, "yamibo", "image_backfill_fixed_after", None))
        ),
    )
