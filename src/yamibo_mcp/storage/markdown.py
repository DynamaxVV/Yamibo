from __future__ import annotations

import json
from dataclasses import asdict

from yamibo_mcp.domain.models import ThreadSnapshot


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
    for floor in data.get("floors", []):
        pid = int(floor["pid"])
        # `image_urls` 作为详情展示用图片集合；导出仅使用 archived_images。
        floor["image_urls"] = list(local_images.get(pid, [])) + list(local_non_export_images.get(pid, []))
        floor["content_image_urls"] = list(local_images.get(pid, []))
        floor["non_export_image_urls"] = list(local_non_export_images.get(pid, []))
        floor["shared_image_urls"] = list(local_shared_images.get(pid, []))
        floor["skipped_image_urls"] = list(skipped_images.get(pid, []))
    data["context_path"] = context_path
    data["archived_images"] = local_images
    data["non_export_images"] = local_non_export_images
    data["shared_images"] = local_shared_images
    data["skipped_image_urls"] = skipped_images
    data["missing_image_urls"] = missing_image_urls or []
    data["missing_shared_image_urls"] = missing_shared_image_urls or []
    return json.dumps(data, ensure_ascii=False, indent=2)
