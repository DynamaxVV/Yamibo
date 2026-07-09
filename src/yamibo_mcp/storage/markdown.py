from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.images import _is_shared_forum_asset

CONTEXT_FORMAT_ARCHIVE_V1 = "archive-md-v1"
CONTEXT_FORMAT_OBSIDIAN_V2 = "obsidian-md-v2"
CLEANER_VERSION_OBSIDIAN_V2 = "cleaner-1.2"


def render_context_by_profile(
    snapshot: ThreadSnapshot,
    *,
    content_kind: str,
    archived_images: dict[int, list[str]] | None = None,
) -> str:
    frontmatter = {
        "tid": snapshot.tid,
        "raw_title": snapshot.raw_title,
        "publisher": snapshot.publisher,
        "content_kind": content_kind,
        "series_name": snapshot.title.core_title_guess,
        "chapter_name": snapshot.title.chapter_name,
        "chapter_index": snapshot.title.chapter_index,
        "chapter_index_end": snapshot.title.chapter_index_end,
        "chapter_title": snapshot.title.chapter_title,
        "group_name": snapshot.title.group_name,
        "author_guess": snapshot.title.author_guess,
        "image_count": snapshot.image_count,
        "archive_status": "complete",
        "parser_version": snapshot.title.parser_version,
        "context_format_version": CONTEXT_FORMAT_ARCHIVE_V1,
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=_json_default) if value is not None else 'null'}")
    lines.extend(["---", "", f"# {snapshot.display_title}", ""])
    for floor in snapshot.floors:
        publisher = floor.publisher or "unknown"
        pub_time = floor.pub_time or ""
        heading = f"## {floor.floor_no}F · {publisher}"
        if pub_time:
            heading += f" · {pub_time}"
        lines.extend([heading, "", _floor_context_body(floor, content_kind=content_kind), ""])
        for image_path in (archived_images or {}).get(floor.pid, []):
            lines.append(f"![image]({image_path})")
        if (archived_images or {}).get(floor.pid):
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_thread_markdown(snapshot: ThreadSnapshot, *, archived_images: dict[int, list[str]] | None = None) -> str:
    return render_archive_markdown(snapshot, archived_images=archived_images)


def render_archive_markdown(snapshot: ThreadSnapshot, *, archived_images: dict[int, list[str]] | None = None) -> str:
    frontmatter = {
        "tid": snapshot.tid,
        "raw_title": snapshot.raw_title,
        "publisher": snapshot.publisher,
        "series_name": snapshot.title.core_title_guess,
        "chapter_name": snapshot.title.chapter_name,
        "chapter_index": snapshot.title.chapter_index,
        "chapter_index_end": snapshot.title.chapter_index_end,
        "chapter_title": snapshot.title.chapter_title,
        "group_name": snapshot.title.group_name,
        "author_guess": snapshot.title.author_guess,
        "image_count": snapshot.image_count,
        "archive_status": "complete",
        "parser_version": snapshot.title.parser_version,
        "context_format_version": CONTEXT_FORMAT_ARCHIVE_V1,
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=_json_default) if value is not None else 'null'}")
    lines.extend(["---", "", f"# {snapshot.display_title}", ""])
    for floor in snapshot.floors:
        publisher = floor.publisher or "unknown"
        pub_time = floor.pub_time or ""
        heading = f"## {floor.floor_no}F · {publisher}"
        if pub_time:
            heading += f" · {pub_time}"
        lines.extend([heading, "", floor.content or "[image-only floor]", ""])
        for image_path in (archived_images or {}).get(floor.pid, []):
            lines.append(f"![image]({image_path})")
        if (archived_images or {}).get(floor.pid):
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _floor_context_body(floor: Any, *, content_kind: str) -> str:
    if floor.content:
        return floor.content
    if content_kind == "comic" and floor.has_images:
        return "[image-only floor]"
    return "[empty floor]"


def render_obsidian_markdown(snapshot: ThreadSnapshot, *, metadata: dict[str, Any], cleaner_output: dict[str, Any]) -> str:
    frontmatter = _build_obsidian_frontmatter(snapshot, metadata=metadata, cleaner_output=cleaner_output)
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=_json_default) if value is not None else 'null'}")
    lines.extend(["---", f"# {snapshot.display_title}", ""])
    for floor in cleaner_output.get("floors", []):
        lines.extend(_render_obsidian_floor(floor))
    return "\n".join(lines).rstrip() + "\n"


def _build_obsidian_frontmatter(snapshot: ThreadSnapshot, *, metadata: dict[str, Any], cleaner_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "tid": snapshot.tid,
        "title": snapshot.display_title or snapshot.raw_title or "",
        "aliases": _unique_strings([snapshot.display_title, snapshot.raw_title]),
        "publisher": snapshot.publisher or "",
        "date": snapshot.pub_time or "",
        "reply_count": int(metadata.get("reply_count") or max(len(snapshot.floors) - 1, 0)),
        "board": metadata.get("board") or metadata.get("forum_name") or "",
        "tags": list(metadata.get("tags") or []),
        "ai_summary": metadata.get("ai_summary") or "",
        "archive_status": metadata.get("archive_status") or "complete",
        "sync_time": metadata.get("sync_time") or "",
        "context_format_version": CONTEXT_FORMAT_OBSIDIAN_V2,
        "cleaner_version": cleaner_output.get("cleaner_version") or CLEANER_VERSION_OBSIDIAN_V2,
        "context_source_hash": cleaner_output.get("source_hash") or "",
        "context_rendered_at": metadata.get("context_rendered_at") or _utc_now(),
        "context_inputs": metadata.get("context_inputs") or {},
    }


def _render_obsidian_floor(floor: dict[str, Any]) -> list[str]:
    publisher = _escape_obsidian_name(str(floor.get("publisher") or "unknown"))
    pub_time = str(floor.get("pub_time") or "")
    heading = f"## {floor.get('floor_no')}F · [[{publisher}]]"
    if pub_time:
        heading += f" · {pub_time}"
    lines = [heading, ""]
    quote = str(floor.get("cleaned_quote") or "").strip()
    if quote:
        target = _render_quote_target(floor.get("quote_target_hints") or [])
        lines.append(f"> [!quote] 引用{target}：")
        for line in quote.splitlines():
            lines.append(f"> {line}" if line else ">")
        lines.append("")
    body = str(floor.get("cleaned_body") or "").strip() or "[empty floor]"
    lines.extend([body, ""])
    for image in floor.get("image_slots") or []:
        if image.get("local_path"):
            lines.append(f"![[{image['local_path']}]]")
    lines.extend(["", f"^f{floor.get('floor_no')}", "", "---", ""])
    return lines


def _render_quote_target(hints: list[Any]) -> str:
    if not hints:
        return ""
    return f" [[{_escape_obsidian_name(str(hints[0]))}]]"


def _escape_obsidian_name(value: str) -> str:
    return re.sub(r"[\[\]#|]", "_", value)


def _unique_strings(values: list[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def render_thread_metadata(
    snapshot: ThreadSnapshot,
    context_path: str,
    *,
    archived_images: dict[int, list[str]] | None = None,
    non_export_images: dict[int, list[str]] | None = None,
    shared_images: dict[int, list[str]] | None = None,
    skipped_image_urls: dict[int, list[str]] | None = None,
    missing_image_urls: list[str] | None = None,
    missing_shared_image_urls: list[str] | None = None,
    context_format_version: str = CONTEXT_FORMAT_ARCHIVE_V1,
    cleaner_version: str | None = None,
    context_source_hash: str | None = None,
    context_rendered_at: str | None = None,
    context_inputs: dict[str, Any] | None = None,
) -> str:
    data = asdict(snapshot)
    local_images = archived_images or {}
    local_non_export_images = non_export_images or {}
    local_shared_images = shared_images or {}
    skipped_images = skipped_image_urls or {}
    missing_images = set(missing_image_urls or [])
    missing_shared = set(missing_shared_image_urls or [])
    for floor in data.get("floors", []):
        pid = int(floor["pid"])
        remote_image_urls = list(floor.get("image_urls") or [])
        floor["remote_image_urls"] = remote_image_urls
        floor["image_urls"] = list(local_images.get(pid, [])) + list(local_non_export_images.get(pid, []))
        floor["content_image_urls"] = list(local_images.get(pid, []))
        floor["non_export_image_urls"] = list(local_non_export_images.get(pid, []))
        floor["shared_image_urls"] = list(local_shared_images.get(pid, []))
        floor["skipped_image_urls"] = list(skipped_images.get(pid, []))
        floor["missing_image_urls"] = [url for url in remote_image_urls if url in missing_images or url in missing_shared]
        floor["image_slots"] = _build_floor_image_slots(
            remote_image_urls,
            content_image_urls=floor["content_image_urls"],
            non_export_image_urls=floor["non_export_image_urls"],
            shared_image_urls=floor["shared_image_urls"],
            skipped_image_urls=floor["skipped_image_urls"],
            missing_image_urls=missing_images,
            missing_shared_image_urls=missing_shared,
        )
    data["context_path"] = context_path
    data["context_format_version"] = context_format_version
    data["cleaner_version"] = cleaner_version or ""
    data["context_source_hash"] = context_source_hash or ""
    data["context_rendered_at"] = context_rendered_at or _utc_now()
    data["context_inputs"] = context_inputs or {}
    data["archived_images"] = local_images
    data["non_export_images"] = local_non_export_images
    data["shared_images"] = local_shared_images
    data["skipped_image_urls"] = skipped_images
    data["missing_image_urls"] = missing_image_urls or []
    data["missing_shared_image_urls"] = missing_shared_image_urls or []
    return json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)


def _build_floor_image_slots(
    remote_image_urls: list[str],
    *,
    content_image_urls: list[str],
    non_export_image_urls: list[str],
    shared_image_urls: list[str],
    skipped_image_urls: list[str],
    missing_image_urls: set[str],
    missing_shared_image_urls: set[str],
) -> list[dict[str, str | None]]:
    slots: list[dict[str, str | None]] = []
    content_index = 0
    non_export_index = 0
    shared_index = 0
    skipped_set = set(skipped_image_urls)
    for remote_url in remote_image_urls:
        if remote_url in skipped_set:
            slots.append({"remote_url": remote_url, "local_path": None, "status": "skipped"})
            continue
        if remote_url in missing_shared_image_urls:
            slots.append({"remote_url": remote_url, "local_path": None, "status": "missing_shared"})
            continue
        if remote_url in missing_image_urls:
            slots.append({"remote_url": remote_url, "local_path": None, "status": "missing"})
            continue
        if _is_shared_forum_asset(remote_url):
            local_path = shared_image_urls[shared_index] if shared_index < len(shared_image_urls) else None
            shared_index += 1 if local_path else 0
            slots.append({"remote_url": remote_url, "local_path": local_path, "status": "shared"})
            continue
        if content_index < len(content_image_urls):
            local_path = content_image_urls[content_index]
            content_index += 1
            slots.append({"remote_url": remote_url, "local_path": local_path, "status": "content"})
            continue
        if non_export_index < len(non_export_image_urls):
            local_path = non_export_image_urls[non_export_index]
            non_export_index += 1
            slots.append({"remote_url": remote_url, "local_path": local_path, "status": "non_export"})
            continue
        slots.append({"remote_url": remote_url, "local_path": None, "status": "missing"})
    return slots
