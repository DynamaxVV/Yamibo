from __future__ import annotations

from dataclasses import dataclass, field

from yamibo_mcp.domain.models import ThreadSnapshot


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _floor_has_content(floor) -> bool:
    return bool((floor.content or "").strip()) or bool(floor.has_images)


def _publisher_identity(name: str | None, uid: str | None) -> tuple[str, str]:
    return (
        name.strip().casefold() if name and name.strip() else "",
        str(uid).strip() if uid and str(uid).strip() else "",
    )


def has_later_external_reply(snapshot: ThreadSnapshot) -> bool:
    """Return whether an empty primary floor has a reply from another user.

    An empty first floor is not automatically a corrupt post: the author may
    have deleted it after other members replied.  We only treat it as a normal
    thread when a later non-empty floor can be attributed to a different
    publisher.  Missing primary publisher metadata is handled conservatively
    by retaining a later non-empty reply rather than discarding the thread.
    """
    if not snapshot.floors:
        return False
    primary = snapshot.floors[0]
    if _floor_has_content(primary):
        return False
    primary_name, primary_uid = _publisher_identity(snapshot.publisher, snapshot.publisher_uid)
    if not primary_name and not primary_uid:
        primary_name, primary_uid = _publisher_identity(
            primary.publisher, primary.publisher_uid
        )
    for floor in snapshot.floors[1:]:
        if not _floor_has_content(floor):
            continue
        reply_name, reply_uid = _publisher_identity(floor.publisher, floor.publisher_uid)
        if not primary_name and not primary_uid:
            return True
        if primary_uid and reply_uid and primary_uid == reply_uid:
            continue
        if primary_name and reply_name and primary_name == reply_name:
            continue
        if reply_name or reply_uid:
            return True
    return False


def empty_primary_floor_exclusion_reason(snapshot: ThreadSnapshot) -> str | None:
    """Return a stable exclusion reason for a non-archivable empty-first-floor post."""
    if not snapshot.floors or _floor_has_content(snapshot.floors[0]):
        return None
    if len(snapshot.floors) == 1:
        return "empty_primary_single_floor"
    if has_later_external_reply(snapshot):
        return None
    return "empty_primary_without_external_reply"


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
            # 只有真正的楼主（floor_no==1）才要求内容/图片必须存在；
            # 同为 thread_publisher 的其他楼层（如连载帖中作者更新的后续楼层）
            # 可能因权限不足被锁定而导致内容为空，这种情况记录 warning 即可，
            # 不应该阻断整个帖子的归档。
            is_primary_content_floor = floor.floor_no == 1
            if is_primary_content_floor:
                if has_later_external_reply(snapshot):
                    warnings.append(
                        f"floor {floor.pid} primary content is empty but later external replies exist"
                    )
                else:
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
