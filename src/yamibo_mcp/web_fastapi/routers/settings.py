from __future__ import annotations

import json
import os
import urllib.request
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException

from yamibo_mcp.config import Settings, load_settings, read_local_config, write_local_config
from yamibo_mcp.services.web_chat import probe_hermes_chat_completions
from yamibo_mcp.web_fastapi.deps import get_settings

router = APIRouter(prefix="/api", tags=["settings"])

SETTINGS_FIELD_SPECS = {
    "db_backend": {"section": "database", "key": "backend", "type": "string", "default": "postgres", "env": "YAMIBO_DB_BACKEND", "effect": "restart_daemon_web"},
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
    "rag_enabled": {"section": "rag", "key": "enabled", "type": "bool", "default": True, "env": "YAMIBO_RAG_ENABLED", "effect": "restart_daemon_web"},
    "rag_base_url": {"section": "rag", "key": "base_url", "type": "string", "default": None, "env": "YAMIBO_RAG_BASE_URL", "inherit": "llm_base_url", "effect": "restart_daemon_web"},
    "rag_api_key": {"section": "rag", "key": "api_key", "type": "string", "default": None, "env": "YAMIBO_RAG_API_KEY", "inherit": "llm_api_key", "sensitive": True, "effect": "restart_daemon_web"},
    "rag_embedding_model": {"section": "rag", "key": "embedding_model", "type": "string", "default": "text-embedding-3-small", "env": "YAMIBO_RAG_EMBEDDING_MODEL", "effect": "restart_daemon_web"},
    "rag_embedding_dimensions": {"section": "rag", "key": "embedding_dimensions", "type": "int", "default": 512, "env": "YAMIBO_RAG_EMBEDDING_DIMENSIONS", "effect": "restart_daemon_web"},
    "rag_chunker_version": {"section": "rag", "key": "chunker_version", "type": "string", "default": "rag-chunker-v1", "env": "YAMIBO_RAG_CHUNKER_VERSION", "effect": "restart_daemon"},
    "rag_min_chunk_chars": {"section": "rag", "key": "min_chunk_chars", "type": "int", "default": 20, "env": "YAMIBO_RAG_MIN_CHUNK_CHARS", "effect": "restart_daemon"},
    "rag_max_chunk_chars": {"section": "rag", "key": "max_chunk_chars", "type": "int", "default": 900, "env": "YAMIBO_RAG_MAX_CHUNK_CHARS", "effect": "restart_daemon"},
    "rag_hybrid_fts_candidates": {"section": "rag", "key": "hybrid_fts_candidates", "type": "int", "default": 50, "env": "YAMIBO_RAG_HYBRID_FTS_CANDIDATES", "effect": "immediate"},
    "rag_hybrid_vector_candidates": {"section": "rag", "key": "hybrid_vector_candidates", "type": "int", "default": 50, "env": "YAMIBO_RAG_HYBRID_VECTOR_CANDIDATES", "effect": "immediate"},
    "rag_debug_indexing": {"section": "rag", "key": "debug_indexing", "type": "bool", "default": False, "env": "YAMIBO_RAG_DEBUG_INDEXING", "effect": "restart_daemon"},
    "title_parse_use_llm": {"section": "title", "key": "use_llm", "type": "bool", "default": True, "env": "YAMIBO_TITLE_PARSE_USE_LLM", "effect": "restart_daemon"},
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
    if value in {"", None}:
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
    if isinstance(value, tuple):
        return list(value)
    return value


def _settings_payload(settings: Settings) -> dict:
    raw_config = read_local_config(settings.config_path)
    values: dict = {}
    stored: dict = {}
    sources: dict = {}
    locked_fields: list[str] = []
    for name, spec in SETTINGS_FIELD_SPECS.items():
        env_name = spec.get("env")
        if env_name and os.environ.get(env_name) not in {None, ""}:
            sources[name] = "env"
            locked_fields.append(name)
        elif spec["section"] in raw_config and isinstance(raw_config.get(spec["section"]), dict) and spec["key"] in raw_config[spec["section"]]:
            sources[name] = "file"
        elif spec.get("inherit"):
            sources[name] = "derived"
        else:
            sources[name] = "default"
        stored[name] = _setting_value_from_raw(raw_config, spec)
        values[name] = _setting_effective_value(settings, name)

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
        "config_path": str(settings.config_path),
        "values": values,
        "stored": stored,
        "sources": sources,
        "locked_fields": locked_fields,
        "effects": {name: spec["effect"] for name, spec in SETTINGS_FIELD_SPECS.items()},
    }


def _normalize_setting_input(name: str, value):
    spec = SETTINGS_FIELD_SPECS[name]
    if spec["type"] == "list":
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return []
    if spec["type"] == "bool":
        return bool(value)
    if spec["type"] == "int":
        return int(value)
    if spec["type"] == "float":
        return float(value)
    if value is None:
        return None
    return str(value).strip()


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
    if value in {None, ""}:
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


@router.post("/settings")
def settings_update(body: dict, settings: Settings = Depends(get_settings)):
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
            hints_updates[name] = _normalize_setting_input(name, values.pop(name))

    for name, value in values.items():
        if name not in SETTINGS_FIELD_SPECS:
            continue
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

    refreshed = load_settings()
    payload = _settings_payload(refreshed)
    payload.update(summarize_setting_effects(changed_fields))
    payload["ok"] = True
    payload["saved_path"] = str(settings.config_path)
    return payload


@router.get("/settings/models")
def settings_models_get(settings: Settings = Depends(get_settings)):
    request = urllib.request.Request(
        settings.llm_base_url.rstrip("/") + "/models",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}),
        },
        method="GET",
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
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
def settings_hermes_test(settings: Settings = Depends(get_settings)):
    return probe_hermes_chat_completions(settings)


def persist_jobs_enabled(settings: Settings, enabled: bool) -> None:
    raw_config = read_local_config(settings.config_path)
    next_raw_config = deepcopy(raw_config)
    _apply_setting_patch(next_raw_config, "jobs_enabled", enabled)
    write_local_config(settings.config_path, next_raw_config)
