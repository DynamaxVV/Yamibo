from __future__ import annotations

from typing import Any

from yamibo_mcp.domain.forums import resolve_forum
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot
from yamibo_mcp.rag.anime_dry_run import build_anime_dry_run_thread
from yamibo_mcp.storage.markdown import CLEANER_VERSION_OBSIDIAN_V2, _build_floor_image_slots


def build_obsidian_context_payload(
    snapshot: ThreadSnapshot,
    *,
    forum_id: int | None,
    archive_status: str,
    reply_count: int | None = None,
    sync_time: str | None = None,
    archived_images: dict[int, list[str]] | None = None,
    non_export_images: dict[int, list[str]] | None = None,
    shared_images: dict[int, list[str]] | None = None,
    skipped_image_urls: dict[int, list[str]] | None = None,
    missing_image_urls: list[str] | None = None,
    missing_shared_image_urls: list[str] | None = None,
    image_slot_overrides: dict[str, dict[str, str | None]] | None = None,
    context_source: str = "archive_materialize",
) -> tuple[dict[str, Any], dict[str, Any]]:
    forum = resolve_forum(forum_id)
    dry_run = build_anime_dry_run_thread(
        thread_row={
            "tid": snapshot.tid,
            "raw_title": snapshot.raw_title,
            "display_title": snapshot.display_title,
            "publisher": snapshot.publisher,
            "pub_time": snapshot.pub_time,
            "forum_id": forum.forum_id,
            "content_kind": forum.content_kind,
            "category": None,
        },
        floor_rows=[_floor_row(floor) for floor in snapshot.floors],
        structured_cleaning=True,
    )
    floor_by_no = {floor.floor_no: floor for floor in dry_run.floors}
    cleaner_output = {
        "cleaner_version": CLEANER_VERSION_OBSIDIAN_V2,
        "source_hash": dry_run.source_hash,
        "rendered_at": dry_run.generated_at,
        "context_inputs": {"source": context_source, "tid": snapshot.tid, "forum_id": forum.forum_id},
        "floors": [
            _render_floor_payload(
                floor,
                dry_run_floor=floor_by_no.get(floor.floor_no),
                archived_images=archived_images or {},
                non_export_images=non_export_images or {},
                shared_images=shared_images or {},
                skipped_image_urls=skipped_image_urls or {},
                missing_image_urls=set(missing_image_urls or []),
                missing_shared_image_urls=set(missing_shared_image_urls or []),
                image_slot_overrides=image_slot_overrides or {},
            )
            for floor in snapshot.floors
        ],
    }
    metadata = {
        "board": forum.name,
        "forum_name": forum.name,
        "reply_count": int(reply_count if reply_count is not None else max(len(snapshot.floors) - 1, 0)),
        "archive_status": archive_status,
        "sync_time": sync_time or "",
        "context_inputs": cleaner_output["context_inputs"],
        "context_rendered_at": dry_run.generated_at,
        "cleaner_version": CLEANER_VERSION_OBSIDIAN_V2,
    }
    return metadata, cleaner_output


def _floor_row(floor: FloorSnapshot) -> dict[str, Any]:
    return {
        "pid": floor.pid,
        "tid": floor.tid,
        "floor_no": floor.floor_no,
        "publisher": floor.publisher,
        "publisher_uid": floor.publisher_uid,
        "pub_time": floor.pub_time,
        "content": floor.content,
        "reply_text": floor.reply_text,
        "quote_text": floor.quote_text,
        "has_images": floor.has_images,
    }


def _render_floor_payload(
    floor: FloorSnapshot,
    *,
    dry_run_floor: Any,
    archived_images: dict[int, list[str]],
    non_export_images: dict[int, list[str]],
    shared_images: dict[int, list[str]],
    skipped_image_urls: dict[int, list[str]],
    missing_image_urls: set[str],
    missing_shared_image_urls: set[str],
    image_slot_overrides: dict[str, dict[str, str | None]],
) -> dict[str, Any]:
    return {
        "pid": floor.pid,
        "floor_no": floor.floor_no,
        "publisher": floor.publisher,
        "pub_time": floor.pub_time,
        "cleaned_body": floor.content if dry_run_floor is None else dry_run_floor.clean.text,
        "cleaned_quote": "" if dry_run_floor is None or dry_run_floor.quote_clean is None else dry_run_floor.quote_clean.text,
        "quote_target_hints": [],
        "image_slots": _build_floor_image_slots(
            list(floor.image_urls),
            content_image_urls=list(archived_images.get(floor.pid, [])),
            non_export_image_urls=list(non_export_images.get(floor.pid, [])),
            shared_image_urls=list(shared_images.get(floor.pid, [])),
            skipped_image_urls=list(skipped_image_urls.get(floor.pid, [])),
            missing_image_urls=missing_image_urls,
            missing_shared_image_urls=missing_shared_image_urls,
            slot_overrides=image_slot_overrides,
        ),
    }
