from __future__ import annotations

import json
from dataclasses import asdict

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.images import _is_shared_forum_asset


def render_thread_markdown(snapshot: ThreadSnapshot, *, archived_images: dict[int, list[str]] | None = None) -> str:
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
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        if value is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
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
        # `image_urls` 作为详情展示用图片集合；导出仅使用 archived_images。
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
    data["archived_images"] = local_images
    data["non_export_images"] = local_non_export_images
    data["shared_images"] = local_shared_images
    data["skipped_image_urls"] = skipped_images
    data["missing_image_urls"] = missing_image_urls or []
    data["missing_shared_image_urls"] = missing_shared_image_urls or []
    return json.dumps(data, ensure_ascii=False, indent=2)


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
