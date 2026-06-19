from __future__ import annotations

import json
from datetime import datetime, timezone

from yamibo_mcp.config import Settings
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.yamibo.title.normalizer import normalize_display_title


def _normalize_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = normalize_display_title(str(value).strip())
    return text or None


def _merge_unique(existing: list[str], *incoming_values: str | None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *incoming_values]:
        normalized = _normalize_name(item)
        if not normalized or normalized in seen:
            continue
        merged.append(normalized)
        seen.add(normalized)
    return merged


def load_title_hints(settings: Settings) -> dict[str, list[str]]:
    path = _resolve_title_hints_path(settings)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        return {
            "scanlation_groups": _merge_unique(_as_list(payload.get("scanlation_groups"))),
            "authors": _merge_unique(_as_list(payload.get("authors"))),
        }
    # 首次迁移兼容：若独立 hints 文件尚不存在，允许从旧配置名单生成初始文件。
    payload = {
        "scanlation_groups": _merge_unique(list(settings.common_scanlation_groups)),
        "authors": _merge_unique(list(settings.common_authors)),
    }
    write_title_hints(settings, payload)
    return payload


def write_title_hints(settings: Settings, payload: dict[str, list[str]]) -> None:
    content = {
        "scanlation_groups": _merge_unique(_as_list(payload.get("scanlation_groups"))),
        "authors": _merge_unique(_as_list(payload.get("authors"))),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(_resolve_title_hints_path(settings), json.dumps(content, ensure_ascii=False, indent=2))


def update_title_hints(
    settings: Settings,
    *,
    group_name: str | None = None,
    author_guess: str | None = None,
) -> dict[str, list[str]]:
    current = load_title_hints(settings)
    updated = {
        "scanlation_groups": _merge_unique(current["scanlation_groups"], group_name),
        "authors": _merge_unique(current["authors"], author_guess),
    }
    write_title_hints(settings, updated)
    return updated


def _as_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _resolve_title_hints_path(settings: Settings):
    legacy_default = settings.project_root / "data" / "title_hints.json"
    if settings.title_hints_path == legacy_default and settings.data_dir != settings.project_root / "data":
        return settings.data_dir / "title_hints.json"
    return settings.title_hints_path


__all__ = ["load_title_hints", "update_title_hints", "write_title_hints"]
