from __future__ import annotations

import hashlib

from yamibo_mcp.domain.forums import resolve_forum
from yamibo_mcp.domain.models import (
    AssetSnapshot,
    ContentBlock,
    FloorSnapshot,
    PostSnapshot,
    ThreadContentSnapshot,
    ThreadSnapshot,
)
from yamibo_mcp.domain.validation import ValidationResult


def classify_content_kind(
    forum_id: int | None,
    *,
    image_count: int,
    word_count: int,
) -> str:
    profile = resolve_forum(forum_id)
    forum_kind = profile.content_kind
    if forum_kind in ("comic", "novel", "discussion"):
        return forum_kind
    if image_count > 0 and word_count < 200:
        return "comic"
    if word_count >= 500 and image_count == 0:
        return "novel"
    if image_count > 0 and word_count >= 200:
        return "mixed"
    return "discussion"


def build_content_snapshot(
    snapshot: ThreadSnapshot,
    *,
    forum_id: int | None = None,
) -> ThreadContentSnapshot:
    resolved_forum_id = forum_id if forum_id is not None else 30
    total_words = sum(len(floor.content) for floor in snapshot.floors)
    content_kind = classify_content_kind(
        resolved_forum_id,
        image_count=snapshot.image_count,
        word_count=total_words,
    )
    assets = _build_assets(snapshot)
    asset_ids_by_url = {asset.remote_url: asset.asset_id for asset in assets}
    posts = _build_posts(snapshot, asset_ids_by_url=asset_ids_by_url)
    return ThreadContentSnapshot(
        tid=snapshot.tid,
        forum_id=resolved_forum_id,
        content_kind=content_kind,
        posts=posts,
        assets=assets,
    )


def validate_by_profile(
    content: ThreadContentSnapshot,
    *,
    archived_image_pids: set[int] | None = None,
) -> ValidationResult:
    kind = content.content_kind
    if kind == "comic":
        return _validate_comic(content, archived_image_pids=archived_image_pids)
    if kind == "novel":
        return _validate_novel(content)
    if kind == "discussion":
        return _validate_discussion(content)
    return _validate_mixed(content, archived_image_pids=archived_image_pids)


def render_context_by_profile(
    snapshot: ThreadSnapshot,
    *,
    content_kind: str,
    archived_images: dict[int, list[str]] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# {snapshot.display_title}")
    lines.append("")
    for floor in snapshot.floors:
        publisher = floor.publisher or "unknown"
        pub_time = floor.pub_time or ""
        heading = f"## {floor.floor_no}F · {publisher}"
        if pub_time:
            heading += f" · {pub_time}"
        lines.extend([heading, ""])
        if floor.content:
            lines.append(floor.content)
        elif content_kind == "comic":
            lines.append("[image-only floor]")
        else:
            lines.append("[empty floor]")
        lines.append("")
        for image_path in (archived_images or {}).get(floor.pid, []):
            lines.append(f"![image]({image_path})")
        if (archived_images or {}).get(floor.pid):
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_posts(snapshot: ThreadSnapshot, *, asset_ids_by_url: dict[str, str]) -> list[PostSnapshot]:
    posts: list[PostSnapshot] = []
    for floor in snapshot.floors:
        blocks = _build_blocks(floor, asset_ids_by_url=asset_ids_by_url)
        posts.append(
            PostSnapshot(
                pid=floor.pid,
                tid=snapshot.tid,
                floor_no=floor.floor_no,
                publisher=floor.publisher,
                pub_time=floor.pub_time,
                content_text=floor.content,
                blocks=blocks,
            )
        )
    return posts


def _build_blocks(floor: FloorSnapshot, *, asset_ids_by_url: dict[str, str]) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    order = 0
    if floor.content:
        blocks.append(
            ContentBlock(
                block_id=f"{floor.pid}_text_{order}",
                pid=floor.pid,
                order_index=order,
                block_type="text",
                text=floor.content,
            )
        )
        order += 1
    for i, url in enumerate(floor.image_urls):
        blocks.append(
            ContentBlock(
                block_id=f"{floor.pid}_img_{i}",
                pid=floor.pid,
                order_index=order,
                block_type="image",
                asset_id=asset_ids_by_url.get(url),
                asset_url=url,
            )
        )
        order += 1
    if not blocks:
        blocks.append(
            ContentBlock(
                block_id=f"{floor.pid}_empty_0",
                pid=floor.pid,
                order_index=0,
                block_type="unknown",
            )
        )
    return blocks


def _build_assets(snapshot: ThreadSnapshot) -> list[AssetSnapshot]:
    assets: list[AssetSnapshot] = []
    seen: set[str] = set()
    for floor in snapshot.floors:
        for url in floor.image_urls:
            if url in seen:
                continue
            seen.add(url)
            asset_type = _classify_asset_type(url)
            assets.append(
                AssetSnapshot(
                    asset_id=_asset_id_for(snapshot.tid, url),
                    tid=snapshot.tid,
                    pid=floor.pid,
                    asset_type=asset_type,
                    remote_url=url,
                    local_path=None,
                    exportable=asset_type == "image",
                    required=_is_required_asset(snapshot, floor, asset_type),
                    status="pending",
                )
            )
    return assets


def _asset_id_for(tid: int, url: str) -> str:
    digest = hashlib.sha256(f"{tid}:{url}".encode("utf-8")).hexdigest()
    return f"asset_{digest[:16]}"


def _classify_asset_type(url: str) -> str:
    lower = url.lower().strip()
    if lower.startswith("data:"):
        return "external_link"
    if lower.startswith("http://data:") or lower.startswith("https://data:"):
        return "external_link"
    if "/static/image/" in url:
        return "shared"
    if any(lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
        return "image"
    if "attachment" in lower or "forum.php" in lower:
        return "attachment"
    return "external_link"


def _is_required_asset(snapshot: ThreadSnapshot, floor: FloorSnapshot, asset_type: str) -> bool:
    if asset_type == "shared":
        return False
    if asset_type != "image":
        return False
    thread_publisher = (snapshot.publisher or "").strip()
    floor_publisher = (floor.publisher or "").strip()
    if thread_publisher and floor_publisher == thread_publisher:
        return True
    return floor.floor_no == 1


def _validate_comic(
    content: ThreadContentSnapshot,
    *,
    archived_image_pids: set[int] | None = None,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    required_assets = [a for a in content.assets if a.required and a.asset_type == "image"]
    if archived_image_pids is not None:
        missing = [a for a in required_assets if a.pid not in archived_image_pids]
        if missing:
            warnings.append(f"missing {len(missing)} required content images")
    else:
        missing = [a for a in required_assets if a.status == "missing"]
        if missing:
            warnings.append(f"missing {len(missing)} required content images")
    has_primary_text = any(
        post.floor_no == 1 and post.content_text.strip() for post in content.posts
    )
    has_primary_image = any(
        a.required and a.asset_type == "image" for a in content.assets
    )
    if not has_primary_text and not has_primary_image:
        errors.append("primary floor has neither text nor required image")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _validate_novel(content: ThreadContentSnapshot) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    has_primary_text = any(
        post.floor_no == 1 and len(post.content_text.strip()) >= 100 for post in content.posts
    )
    if not has_primary_text:
        errors.append("novel thread requires substantial primary text")
    missing_non_critical = [
        a for a in content.assets
        if a.asset_type == "image" and not a.required and a.status == "missing"
    ]
    if missing_non_critical:
        warnings.append(f"missing {len(missing_non_critical)} non-critical images")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _validate_discussion(content: ThreadContentSnapshot) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    has_content = any(post.content_text.strip() for post in content.posts)
    if not has_content:
        errors.append("discussion thread requires at least one post with text")
    missing_optional = [
        a for a in content.assets
        if a.asset_type == "image" and not a.required and a.status == "missing"
    ]
    if missing_optional:
        warnings.append(f"missing {len(missing_optional)} optional images")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _validate_mixed(
    content: ThreadContentSnapshot,
    *,
    archived_image_pids: set[int] | None = None,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    has_any_content = any(
        post.content_text.strip() or any(b.block_type == "image" for b in post.blocks)
        for post in content.posts
    )
    if not has_any_content:
        errors.append("mixed thread requires at least some text or images")
    unknown_blocks = [
        b for post in content.posts for b in post.blocks if b.block_type == "unknown"
    ]
    if unknown_blocks:
        warnings.append(f"{len(unknown_blocks)} unrecognized content blocks preserved as unknown")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)
