from __future__ import annotations

from dataclasses import dataclass, field

from yamibo_mcp.domain.models import ThreadSnapshot


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_thread_snapshot(snapshot: ThreadSnapshot) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    # 先检查线程级主键和页面类型，避免明显错误的快照进入落库阶段。
    if snapshot.tid <= 0:
        errors.append("tid must be positive")
    if snapshot.page_type != "thread_detail":
        errors.append(f"page_type must be thread_detail, got {snapshot.page_type}")
    if not snapshot.raw_title:
        errors.append("raw_title is required")
    if not snapshot.display_title:
        errors.append("display_title is required")
    if not snapshot.floors:
        errors.append("at least one floor is required")
    if not snapshot.title.display_title:
        errors.append("title.display_title is required")
    if not snapshot.title.core_title_guess:
        errors.append("title.core_title_guess is required")
    if not snapshot.title.series_key:
        warnings.append("title.series_key is missing")

    seen_pids: set[int] = set()
    thread_publisher = (snapshot.publisher or "").strip()
    primary_floor_publisher = (snapshot.floors[0].publisher or "").strip() if snapshot.floors else ""
    main_publishers = {name for name in {thread_publisher, primary_floor_publisher} if name}
    for floor in snapshot.floors:
        if floor.tid != snapshot.tid:
            errors.append(f"floor {floor.pid} tid mismatch")
        if floor.pid <= 0:
            errors.append("floor pid must be positive")
        if floor.pid in seen_pids:
            errors.append(f"duplicate pid {floor.pid}")
        seen_pids.add(floor.pid)
        if floor.floor_no <= 0:
            errors.append(f"floor {floor.pid} floor_no must be positive")
        # 楼主楼层需要严格，普通回复楼如果被清洗成空则记 warning，不阻断整帖归档。
        if not floor.content and not floor.has_images:
            floor_publisher = (floor.publisher or "").strip()
            is_primary_content_floor = floor.floor_no == 1 or (floor_publisher and floor_publisher in main_publishers)
            if is_primary_content_floor:
                errors.append(f"floor {floor.pid} content is required when no images are present")
            else:
                warnings.append(f"floor {floor.pid} is empty and skipped as reply floor")
        if floor.has_images and not floor.image_urls:
            warnings.append(f"floor {floor.pid} has image flag but no image urls")

    if snapshot.title.needs_review:
        warnings.append("title needs review")
    if snapshot.image_count < 0:
        errors.append("image_count must not be negative")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)
