from __future__ import annotations

import json
import os
import math
import hashlib
from threading import RLock
from urllib.parse import urlsplit
import urllib.request
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException

from yamibo_mcp.config import RAG_CHUNKER_VERSION, Settings, load_settings, read_local_config, write_local_config
from yamibo_mcp.web_fastapi.deps import get_chat_service, get_settings
from yamibo_mcp.services.web_chat import ChatService

router = APIRouter(prefix="/api", tags=["settings"])
_CONFIG_LOCK = RLock()

TABLE_LAYOUT_DEFAULTS = {
    "threads": [
        {"key": "tid", "visible": True, "width": 60},
        {"key": "title", "visible": True, "width": 480},
        {"key": "forum", "visible": True, "width": 80},
        {"key": "category", "visible": True, "width": 110},
        {"key": "archive", "visible": True, "width": 60},
        {"key": "reply_count", "visible": True, "width": 55},
        {"key": "pub_time", "visible": True, "width": 105},
        {"key": "last_reply_time", "visible": True, "width": 105},
        {"key": "sync_time", "visible": True, "width": 105},
        {"key": "action", "visible": True, "width": 84},
    ],
    "jobs": [
        {"key": "tid", "visible": True, "width": 65},
        {"key": "description", "visible": True, "width": 420},
        {"key": "status", "visible": True, "width": 80},
        {"key": "stage", "visible": True, "width": 120},
        {"key": "progress", "visible": True, "width": 80},
        {"key": "created_at", "visible": True, "width": 110},
        {"key": "action", "visible": True, "width": 110},
    ],
}

SETTINGS_FIELD_SPECS = {
    "chat_backend": {"section": "chat", "key": "backend", "type": "string", "default": "hermes", "env": "YAMIBO_CHAT_BACKEND", "effect": "restart_web"},
    "settings_access_token": {"section": "security", "key": "access_token", "type": "string", "default": None, "env": "YAMIBO_SETTINGS_ACCESS_TOKEN", "sensitive": True, "effect": "restart_web", "readonly": True},
    "chat_max_parallel": {"section": "chat", "key": "max_parallel", "type": "int", "default": 2, "env": "YAMIBO_CHAT_MAX_PARALLEL", "effect": "restart_web"},
    "chat_batch_limit": {"section": "chat", "key": "batch_limit", "type": "int", "default": 20, "env": "YAMIBO_CHAT_BATCH_LIMIT", "effect": "restart_web"},
    "auto_signin_enabled": {"section": "worker", "key": "auto_signin_enabled", "type": "bool", "default": True, "env": "YAMIBO_AUTO_SIGNIN_ENABLED", "effect": "immediate"},
    "cookie_refresh_interval_hours": {"section": "yamibo", "key": "cookie_refresh_interval_hours", "type": "float", "default": 12, "env": "YAMIBO_COOKIE_REFRESH_INTERVAL_HOURS", "effect": "restart_daemon"},
    "image_backfill_enabled": {"section": "yamibo", "key": "image_backfill_enabled", "type": "bool", "default": True, "env": "YAMIBO_IMAGE_BACKFILL_ENABLED", "effect": "immediate"},
    "image_backfill_dry_run": {"section": "yamibo", "key": "image_backfill_dry_run", "type": "bool", "default": True, "env": "YAMIBO_IMAGE_BACKFILL_DRY_RUN", "effect": "immediate"},
    "image_backfill_forum_id": {"section": "yamibo", "key": "image_backfill_forum_id", "type": "int", "default": 5, "env": "YAMIBO_IMAGE_BACKFILL_FORUM_ID", "effect": "immediate"},
    "image_backfill_daily_limit": {"section": "yamibo", "key": "image_backfill_daily_limit", "type": "int", "default": 100, "env": "YAMIBO_IMAGE_BACKFILL_DAILY_LIMIT", "effect": "immediate"},
    "image_backfill_auto_interval_seconds": {"section": "yamibo", "key": "image_backfill_auto_interval_seconds", "type": "float", "default": 60, "env": "YAMIBO_IMAGE_BACKFILL_AUTO_INTERVAL_SECONDS", "effect": "immediate"},
    "image_backfill_max_pages": {"section": "yamibo", "key": "image_backfill_max_pages", "type": "int", "default": 1, "env": "YAMIBO_IMAGE_BACKFILL_MAX_PAGES", "effect": "immediate"},
    "chat_max_requests": {"section": "chat", "key": "max_requests", "type": "int", "default": 20, "env": "YAMIBO_CHAT_MAX_REQUESTS", "effect": "restart_web"},
    "chat_max_tools": {"section": "chat", "key": "max_tools", "type": "int", "default": 50, "env": "YAMIBO_CHAT_MAX_TOOLS", "effect": "restart_web"},
    "chat_timeout": {"section": "chat", "key": "timeout", "type": "int", "default": 900, "env": "YAMIBO_CHAT_TIMEOUT", "effect": "restart_web"},

    "db_backend": {"section": "database", "key": "backend", "type": "string", "default": "sqlite", "env": "YAMIBO_DB_BACKEND", "effect": "restart_daemon_web"},
    "db_url": {"section": "database", "key": "url", "type": "string", "default": None, "env": "YAMIBO_DB_URL", "sensitive": True, "effect": "restart_daemon_web"},
    "db_pool_min": {"section": "database", "key": "pool_min", "type": "int", "default": 1, "env": "YAMIBO_DB_POOL_MIN", "effect": "restart_daemon_web"},
    "db_pool_max": {"section": "database", "key": "pool_max", "type": "int", "default": 5, "env": "YAMIBO_DB_POOL_MAX", "effect": "restart_daemon_web"},
    "db_pool_timeout": {"section": "database", "key": "pool_timeout", "type": "float", "default": 30.0, "env": "YAMIBO_DB_POOL_TIMEOUT", "effect": "restart_daemon_web"},
    "db_connect_timeout": {"section": "database", "key": "connect_timeout", "type": "float", "default": 10.0, "env": "YAMIBO_DB_CONNECT_TIMEOUT", "effect": "restart_daemon_web"},
    "db_schema": {"section": "database", "key": "schema", "type": "string", "default": "public", "env": "YAMIBO_DB_SCHEMA", "effect": "restart_daemon_web"},
    "db_ssl_mode": {"section": "database", "key": "ssl_mode", "type": "string", "default": "prefer", "env": "YAMIBO_DB_SSL_MODE", "effect": "restart_daemon_web"},
    "db_ssl_root_cert": {"section": "database", "key": "ssl_root_cert", "type": "string", "default": None, "env": "YAMIBO_DB_SSL_ROOT_CERT", "effect": "restart_daemon_web"},
    "request_timeout_seconds": {"section": "yamibo", "key": "request_timeout_seconds", "type": "float", "default": 30.0, "env": "YAMIBO_REQUEST_TIMEOUT_SECONDS", "effect": "immediate"},
    "request_interval_seconds": {"section": "yamibo", "key": "request_interval_seconds", "type": "float", "default": 1.0, "env": "YAMIBO_REQUEST_INTERVAL_SECONDS", "effect": "immediate"},
    "request_interval_jitter_seconds": {"section": "yamibo", "key": "request_interval_jitter_seconds", "type": "float", "default": 0.5, "env": "YAMIBO_REQUEST_INTERVAL_JITTER_SECONDS", "effect": "immediate"},
    "use_system_proxy": {"section": "yamibo", "key": "use_system_proxy", "type": "bool", "default": False, "env": "YAMIBO_USE_SYSTEM_PROXY", "effect": "immediate"},
    "image_download_timeout_seconds": {"section": "yamibo", "key": "image_download_timeout_seconds", "type": "float", "default": 45.0, "env": "YAMIBO_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "effect": "restart_daemon"},
    "image_download_retries": {"section": "yamibo", "key": "image_download_retries", "type": "int", "default": 2, "env": "YAMIBO_IMAGE_DOWNLOAD_RETRIES", "effect": "restart_daemon"},
    "archive_thread_max_pages": {"section": "yamibo", "key": "archive_thread_max_pages", "type": "int", "default": 50, "env": "YAMIBO_ARCHIVE_THREAD_MAX_PAGES", "effect": "restart_daemon"},
    "novel_author_only_max_pages": {"section": "yamibo", "key": "novel_author_only_max_pages", "type": "int", "default": 50, "env": "YAMIBO_NOVEL_AUTHOR_ONLY_MAX_PAGES", "effect": "restart_daemon"},
    "novel_author_only_page_delay_seconds": {"section": "yamibo", "key": "novel_author_only_page_delay_seconds", "type": "float", "default": 0.5, "env": "YAMIBO_NOVEL_AUTHOR_ONLY_PAGE_DELAY_SECONDS", "effect": "restart_daemon"},
    "llm_base_url": {"section": "llm", "key": "base_url", "type": "string", "default": "https://api.openai.com/v1", "env": "YAMIBO_LLM_BASE_URL", "effect": "restart_daemon_web"},
    "llm_api_key": {"section": "llm", "key": "api_key", "type": "string", "default": None, "env": "YAMIBO_LLM_API_KEY", "sensitive": True, "effect": "restart_daemon_web"},
    "llm_model": {"section": "llm", "key": "model", "type": "string", "default": "gpt-4.1-mini", "env": "YAMIBO_LLM_MODEL", "effect": "restart_daemon_web"},
    "hermes_host": {"section": "chat", "key": "hermes_host", "type": "string", "default": "host.docker.internal", "env": "YAMIBO_HERMES_HOST", "effect": "restart_web"},
    "hermes_port": {"section": "chat", "key": "hermes_port", "type": "int", "default": 8642, "env": "YAMIBO_HERMES_PORT", "effect": "restart_web"},
    "hermes_model": {"section": "chat", "key": "hermes_model", "type": "string", "default": "hermes-agent", "env": "YAMIBO_HERMES_MODEL", "effect": "restart_web"},
    "hermes_api_key": {"section": "chat", "key": "hermes_api_key", "type": "string", "default": None, "env": "YAMIBO_HERMES_API_KEY", "sensitive": True, "effect": "restart_web"},
    "chat_streaming_enabled": {"section": "ui", "key": "chat_streaming_enabled", "type": "bool", "default": True, "env": None, "ui_only": True, "effect": "immediate"},
    "rag_enabled": {"section": "rag", "key": "enabled", "type": "bool", "default": True, "env": "YAMIBO_RAG_ENABLED", "effect": "restart_daemon_web"},
    "rag_base_url": {"section": "rag", "key": "base_url", "type": "string", "default": None, "env": "YAMIBO_RAG_BASE_URL", "inherit": "llm_base_url", "effect": "restart_daemon_web"},
    "rag_api_key": {"section": "rag", "key": "api_key", "type": "string", "default": None, "env": "YAMIBO_RAG_API_KEY", "inherit": "llm_api_key", "sensitive": True, "effect": "restart_daemon_web"},
    "rag_embedding_model": {"section": "rag", "key": "embedding_model", "type": "string", "default": "text-embedding-3-small", "env": "YAMIBO_RAG_EMBEDDING_MODEL", "effect": "restart_daemon_web"},
    "rag_embedding_dimensions": {"section": "rag", "key": "embedding_dimensions", "type": "int", "default": 512, "env": "YAMIBO_RAG_EMBEDDING_DIMENSIONS", "effect": "restart_daemon_web"},
    "rag_hybrid_fts_candidates": {"section": "rag", "key": "hybrid_fts_candidates", "type": "int", "default": 50, "env": "YAMIBO_RAG_HYBRID_FTS_CANDIDATES", "effect": "immediate"},
    "rag_hybrid_vector_candidates": {"section": "rag", "key": "hybrid_vector_candidates", "type": "int", "default": 50, "env": "YAMIBO_RAG_HYBRID_VECTOR_CANDIDATES", "effect": "immediate"},
    "rag_debug_indexing": {"section": "rag", "key": "debug_indexing", "type": "bool", "default": False, "env": "YAMIBO_RAG_DEBUG_INDEXING", "effect": "restart_daemon"},
    "title_parse_mode": {"section": "title", "key": "parse_mode", "type": "string", "default": "always", "env": "YAMIBO_TITLE_PARSE_MODE", "effect": "restart_daemon"},
    "common_scanlation_groups": {"section": "title", "key": "common_scanlation_groups", "type": "list", "default": [], "env": None, "effect": "restart_daemon"},
    "common_authors": {"section": "title", "key": "common_authors", "type": "list", "default": [], "env": None, "effect": "restart_daemon"},
    "export_default_strategy": {"section": "export", "key": "default_strategy", "type": "string", "default": "cache_only", "env": "YAMIBO_EXPORT_DEFAULT_STRATEGY", "effect": "restart_daemon_web"},
    "export_stale_after_hours": {"section": "export", "key": "stale_after_hours", "type": "int", "default": 24, "env": "YAMIBO_EXPORT_STALE_AFTER_HOURS", "effect": "restart_daemon_web"},
    "novel_txt_include_filtered_notes": {"section": "export", "key": "novel_txt_include_filtered_notes", "type": "bool", "default": False, "env": "YAMIBO_NOVEL_TXT_INCLUDE_FILTERED_NOTES", "effect": "restart_daemon"},
    "novel_txt_debug_markers": {"section": "export", "key": "novel_txt_debug_markers", "type": "bool", "default": False, "env": "YAMIBO_NOVEL_TXT_DEBUG_MARKERS", "effect": "restart_daemon"},
    "backup_keep_count": {"section": "maintenance", "key": "backup_keep_count", "type": "int", "default": 20, "env": "YAMIBO_BACKUP_KEEP_COUNT", "effect": "immediate"},
    "cleanup_staging_older_than_hours": {"section": "maintenance", "key": "cleanup_staging_older_than_hours", "type": "int", "default": 48, "env": "YAMIBO_CLEANUP_STAGING_OLDER_THAN_HOURS", "effect": "immediate"},
    "jobs_enabled": {"section": "worker", "key": "jobs_enabled", "type": "bool", "default": True, "env": "YAMIBO_JOBS_ENABLED", "effect": "immediate"},
    "worker_poll_seconds": {"section": "worker", "key": "poll_seconds", "type": "float", "default": 2.0, "env": "YAMIBO_WORKER_POLL_SECONDS", "effect": "restart_daemon"},
    "worker_lease_seconds": {"section": "worker", "key": "lease_seconds", "type": "int", "default": 60, "env": "YAMIBO_WORKER_LEASE_SECONDS", "effect": "restart_daemon"},
    "worker_heartbeat_seconds": {"section": "worker", "key": "heartbeat_seconds", "type": "int", "default": 15, "env": "YAMIBO_WORKER_HEARTBEAT_SECONDS", "effect": "restart_daemon"},
    "worker_parallelism": {"section": "worker", "key": "parallelism", "type": "int", "default": 2, "env": "YAMIBO_WORKER_PARALLELISM", "effect": "restart_daemon"},
    "table_layouts": {"section": "ui", "key": "table_layouts", "type": "json", "default": TABLE_LAYOUT_DEFAULTS, "env": None, "effect": "immediate"},
}

SETTINGS_RESTART_TARGETS = {
    "immediate": [],
    "restart_daemon": ["daemon"],
    "restart_web": ["web"],
    "restart_daemon_web": ["daemon", "web"],
}


def _setting_value_from_raw(raw_config: dict, spec: dict):
    section = raw_config.get(spec["section"], {})
    if not isinstance(section, dict) or spec["key"] not in section:
        return None
    value = section.get(spec["key"])
    if spec["type"] == "json":
        return deepcopy(value) if isinstance(value, (dict, list)) else None
    if value is None or value == "":
        return None
    if spec["type"] == "list":
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return None
    if spec["type"] == "bool":
        return bool(value)
    if spec["type"] == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if spec["type"] == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return str(value)


def _setting_effective_value(settings: Settings, name: str):
    value = getattr(settings, name, SETTINGS_FIELD_SPECS[name].get("default"))
    if name == "title_parse_mode" and value is None:
        return "always" if getattr(settings, "title_parse_use_llm", True) else "rules_only"
    if isinstance(value, tuple):
        return list(value)
    return value


def _settings_payload(settings: Settings) -> dict:
    raw_config = read_local_config(settings.config_path)
    values: dict = {}
    stored: dict = {}
    sources: dict = {}
    locked_fields: list[str] = []
    configured: dict[str, bool] = {}
    active_values: dict = {}
    pending_fields: list[str] = []
    for name, spec in SETTINGS_FIELD_SPECS.items():
        env_name = spec.get("env")
        if name == "settings_access_token" and not os.environ.get(env_name):
            env_name = "YAMIBO_CHAT_ACCESS_TOKEN"
        if name == "title_parse_mode" and not os.environ.get(env_name) and "YAMIBO_TITLE_PARSE_USE_LLM" in os.environ:
            env_name = "YAMIBO_TITLE_PARSE_USE_LLM"
        if env_name and os.environ.get(env_name) not in {None, ""}:
            sources[name] = "env"
            locked_fields.append(name)
        elif spec["section"] in raw_config and isinstance(raw_config.get(spec["section"]), dict) and spec["key"] in raw_config[spec["section"]]:
            sources[name] = "file"
        elif spec.get("inherit"):
            sources[name] = "derived"
        else:
            sources[name] = "default"
        stored_value = _setting_value_from_raw(raw_config, spec)
        if name == "settings_access_token" and stored_value is None:
            stored_value = raw_config.get("chat", {}).get("access_token")
            if stored_value and sources[name] == "default":
                sources[name] = "file"
        effective_value = _setting_effective_value(settings, name)
        if spec.get("sensitive"):
            configured[name] = effective_value not in {None, ""} or stored_value not in {None, ""} or (env_name and os.environ.get(env_name) not in {None, ""}) or bool(spec.get("inherit") and configured.get(spec["inherit"]))
            stored[name] = None
            values[name] = None
        else:
            stored[name] = stored_value
            values[name] = stored_value if sources[name] == "file" and stored_value is not None else effective_value
        active_values[name] = None if spec.get("sensitive") else effective_value
        if not spec.get("sensitive") and values[name] != active_values[name]:
            pending_fields.append(name)

    try:
        from yamibo_mcp.services.title_hints import load_title_hints
        hints = load_title_hints(settings)
        values["common_scanlation_groups"] = hints["scanlation_groups"]
        values["common_authors"] = hints["authors"]
        stored["common_scanlation_groups"] = hints["scanlation_groups"]
        stored["common_authors"] = hints["authors"]
        sources["common_scanlation_groups"] = "file"
        sources["common_authors"] = "file"
    except Exception:
        pass

    return {
        "revision": hashlib.sha256(json.dumps({"config": raw_config, "authors": values.get("common_authors"), "groups": values.get("common_scanlation_groups")}, sort_keys=True).encode()).hexdigest(),
        "config_path": str(settings.config_path),
        "values": values,
        "stored": stored,
        "sources": sources,
        "locked_fields": locked_fields,
        "configured": configured,
        "active_values": active_values,
        "pending_fields": pending_fields,
        "readonly_fields": [name for name, spec in SETTINGS_FIELD_SPECS.items() if spec.get("readonly") or spec["section"] == "database"],
        "chunker_version": RAG_CHUNKER_VERSION,
        "effects": {name: spec["effect"] for name, spec in SETTINGS_FIELD_SPECS.items()},
    }


def _normalize_setting_input(name: str, value):
    if name == "export_default_strategy" and value not in ("cache_only", "sync_if_stale", "force_resync"):
        raise ValueError("export_default_strategy must be cache_only, sync_if_stale or force_resync")
    if name == "title_parse_mode" and value not in ("rules_only", "fallback", "always"):
        raise ValueError("title_parse_mode must be rules_only, fallback or always")
    if name == "chat_backend" and value not in ("hermes", "embedded"):
        raise ValueError("chat_backend must be hermes or embedded")
    spec = SETTINGS_FIELD_SPECS[name]
    if spec["type"] in {"int", "float"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        if spec["type"] == "int" and not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        minimum = 0 if name in {"request_interval_seconds", "request_interval_jitter_seconds", "image_download_retries", "novel_author_only_page_delay_seconds"} else (0.01 if spec["type"] == "float" else 1)
        maximum = 65535 if name == "hermes_port" else (64 if name in {"worker_parallelism", "chat_max_parallel"} else 1000000)
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
    if name.endswith("base_url") and value:
        parsed = urlsplit(value) if isinstance(value, str) else None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"{name} must be an HTTP(S) URL without credentials")
    if spec["type"] == "json":
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be an object")
        normalized = deepcopy(TABLE_LAYOUT_DEFAULTS)
        for table_name in normalized:
            entries = value.get(table_name)
            if not isinstance(entries, list):
                continue
            seen: set[str] = set()
            clean_entries = []
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                    continue
                key = item["key"]
                if key in seen or not any(default["key"] == key for default in normalized[table_name]):
                    continue
                seen.add(key)
                width = item.get("width")
                if not isinstance(width, (int, float)) or width <= 0:
                    width = next(default["width"] for default in normalized[table_name] if default["key"] == key)
                clean_entries.append({"key": key, "visible": bool(item.get("visible", True)), "width": width})
            if clean_entries:
                normalized[table_name] = clean_entries
        return normalized
    if spec["type"] == "list":
        if value is None:
            return []
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ValueError(f"{name} items must be text")
            return list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        raise ValueError(f"{name} must be a list or text")
    if spec["type"] == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value
    if spec["type"] == "int":
        return int(value)
    if spec["type"] == "float":
        return float(value)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    return value.strip()


def _apply_setting_patch(raw_config: dict, name: str, value) -> None:
    spec = SETTINGS_FIELD_SPECS[name]
    section = raw_config.setdefault(spec["section"], {})
    if not isinstance(section, dict):
        raise ValueError(f"invalid config section: {spec['section']}")
    if spec["type"] == "list":
        items = _normalize_setting_input(name, value)
        if items:
            section[spec["key"]] = items
        else:
            section.pop(spec["key"], None)
        if not section:
            raw_config.pop(spec["section"], None)
        return
    if spec["type"] == "json":
        section[spec["key"]] = _normalize_setting_input(name, value)
        return
    if value is None or value == "":
        if spec.get("inherit") or spec["type"] == "string":
            section.pop(spec["key"], None)
            if not section:
                raw_config.pop(spec["section"], None)
            return
        raise ValueError(f"{name} is required")
    normalized = _normalize_setting_input(name, value)
    section[spec["key"]] = normalized


def summarize_setting_effects(field_names: list[str]) -> dict:
    targets: list[str] = []
    for name in field_names:
        for target in SETTINGS_RESTART_TARGETS[SETTINGS_FIELD_SPECS[name]["effect"]]:
            if target not in targets:
                targets.append(target)
    summary = "immediate"
    if targets == ["daemon"]:
        summary = "restart_daemon"
    elif targets == ["web"]:
        summary = "restart_web"
    elif "daemon" in targets and "web" in targets:
        summary = "restart_daemon_web"
    return {
        "restart_required": bool(targets),
        "restart_targets": targets,
        "effect_mode_summary": summary,
    }


@router.get("/settings")
def settings_get(settings: Settings = Depends(get_settings)):
    return _settings_payload(settings)


@router.get("/settings-layouts")
def settings_layouts_get(settings: Settings = Depends(get_settings)):
    """Return the non-sensitive display preference used by ordinary list pages."""
    raw = read_local_config(settings.config_path)
    stored = _setting_value_from_raw(raw, SETTINGS_FIELD_SPECS["table_layouts"])
    return {"table_layouts": stored or deepcopy(TABLE_LAYOUT_DEFAULTS)}


@router.post("/settings")
def settings_update(body: dict, settings: Settings = Depends(get_settings)):
    with _CONFIG_LOCK:
        return _settings_update(body, settings)


def _settings_update(body: dict, settings: Settings):
    if body.get("revision") is not None and body["revision"] != _settings_payload(settings)["revision"]:
        raise HTTPException(409, detail="设置已被其他会话修改，请重新加载后再保存")
    values = body.get("values")
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="values required")
    locked_fields = set(_settings_payload(settings)["locked_fields"])
    raw_config = read_local_config(settings.config_path)
    next_raw_config = deepcopy(raw_config)
    changed_fields: list[str] = []

    HINTS_FIELDS = {"common_scanlation_groups", "common_authors"}
    hints_updates: dict[str, list[str]] = {}
    for name in list(values.keys()):
        if name in HINTS_FIELDS:
            try:
                hints_updates[name] = _normalize_setting_input(name, values.pop(name))
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, detail=str(exc))

    for name, value in values.items():
        if name not in SETTINGS_FIELD_SPECS:
            raise HTTPException(status_code=400, detail=f"Unknown setting: {name}")
        if SETTINGS_FIELD_SPECS[name].get("readonly") or SETTINGS_FIELD_SPECS[name]["section"] == "database":
            raise HTTPException(status_code=400, detail=f"{name} is read-only")
        if name in locked_fields:
            raise HTTPException(status_code=400, detail=f"{name} is overridden by environment variable")
        before = _setting_value_from_raw(raw_config, SETTINGS_FIELD_SPECS[name])
        try:
            _apply_setting_patch(next_raw_config, name, value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        after = _setting_value_from_raw(next_raw_config, SETTINGS_FIELD_SPECS[name])
        if before != after and name not in changed_fields:
            changed_fields.append(name)
    effective = dict(_settings_payload(settings)["values"])
    effective.update({name: _setting_value_from_raw(next_raw_config, SETTINGS_FIELD_SPECS[name]) for name in values})
    if effective["worker_heartbeat_seconds"] >= effective["worker_lease_seconds"]:
        raise HTTPException(400, detail="worker_heartbeat_seconds must be less than worker_lease_seconds")
    write_local_config(settings.config_path, next_raw_config)

    if hints_updates:
        from yamibo_mcp.services.title_hints import load_title_hints, write_title_hints
        current = load_title_hints(settings)
        scanlation_groups = hints_updates.get("common_scanlation_groups")
        authors = hints_updates.get("common_authors")
        write_title_hints(settings, {
            "scanlation_groups": scanlation_groups if scanlation_groups is not None else current["scanlation_groups"],
            "authors": authors if authors is not None else current["authors"],
        })
        changed_fields.extend(hints_updates.keys())

    payload = _settings_payload(settings)
    # UI-only JSON settings are intentionally not part of the runtime Settings dataclass.
    # Return the just-written value even when this endpoint is using an injected test/runtime config.
    if "table_layouts" in next_raw_config.get("ui", {}):
        payload["values"]["table_layouts"] = next_raw_config["ui"]["table_layouts"]
        payload["stored"]["table_layouts"] = next_raw_config["ui"]["table_layouts"]
        payload["sources"]["table_layouts"] = "file"
    payload.update(summarize_setting_effects(changed_fields))
    payload["ok"] = True
    payload["saved_path"] = str(settings.config_path)
    return payload


@router.get("/settings/models")
def settings_models_get(settings: Settings = Depends(get_settings)):
    payload = _settings_payload(settings)
    raw = read_local_config(settings.config_path)
    api_key = settings.llm_api_key if os.environ.get("YAMIBO_LLM_API_KEY") else raw.get("llm", {}).get("api_key", settings.llm_api_key)
    return _fetch_models(payload["values"]["llm_base_url"], api_key)


@router.post("/settings/models")
def settings_models_test(body: dict, settings: Settings = Depends(get_settings)):
    base_url = body.get("base_url", _settings_payload(settings)["values"]["llm_base_url"])
    try:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url is required")
        _normalize_setting_input("llm_base_url", base_url)
        key = body.get("api_key")
        if key is not None:
            _normalize_setting_input("llm_api_key", key)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, detail=str(exc))
    raw = read_local_config(settings.config_path)
    if not key:
        key = settings.llm_api_key if os.environ.get("YAMIBO_LLM_API_KEY") else raw.get("llm", {}).get("api_key", settings.llm_api_key)
    return _fetch_models(base_url, key)


def _fetch_models(base_url: str, api_key: str | None):
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="GET",
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=502, detail="无法连接模型服务，请检查地址和凭据")
    models: list[str] = []
    if isinstance(data, dict):
        items = data.get("data")
        if not isinstance(items, list):
            items = data.get("models")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    model_id = item.get("id") or item.get("model") or item.get("name")
                    if model_id:
                        models.append(str(model_id))
                elif item:
                    models.append(str(item))
    return {"models": sorted(dict.fromkeys(models))}


@router.post("/settings/hermes-test")
def settings_hermes_test(service: ChatService = Depends(get_chat_service)):
    return service.context(force=True)


def persist_jobs_enabled(settings: Settings, enabled: bool) -> None:
    raw_config = read_local_config(settings.config_path)
    next_raw_config = deepcopy(raw_config)
    _apply_setting_patch(next_raw_config, "jobs_enabled", enabled)
    write_local_config(settings.config_path, next_raw_config)
