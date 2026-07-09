from __future__ import annotations

from pathlib import Path

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.images import materialize_staged_images
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.markdown import render_archive_markdown, render_obsidian_markdown, render_thread_metadata
from yamibo_mcp.storage.paths import StoragePaths


def materialize_thread(
    paths: StoragePaths,
    snapshot: ThreadSnapshot,
    *,
    job_id: str | None = None,
    archived_images: dict[int, list[str]] | None = None,
    non_export_images: dict[int, list[str]] | None = None,
    shared_images: dict[int, list[str]] | None = None,
    skipped_image_urls: dict[int, list[str]] | None = None,
    missing_image_urls: list[str] | None = None,
    missing_shared_image_urls: list[str] | None = None,
    context_format_version: str = "archive-md-v1",
    cleaner_output: dict | None = None,
    metadata: dict | None = None,
) -> tuple[Path, Path]:
    context_path = paths.thread_context(snapshot.tid)
    metadata_path = paths.thread_metadata(snapshot.tid)
    relative_context = str(context_path.relative_to(paths.data_dir))
    if job_id:
        materialize_staged_images(paths, job_id, snapshot.tid)
    cleaner_output = cleaner_output or {}
    metadata_payload = dict(metadata or {})
    metadata_payload.setdefault("context_format_version", context_format_version)
    metadata_payload.setdefault("context_source_hash", cleaner_output.get("source_hash", ""))
    metadata_payload.setdefault("context_rendered_at", cleaner_output.get("rendered_at", ""))
    metadata_payload.setdefault("cleaner_version", cleaner_output.get("cleaner_version", ""))
    metadata_payload.setdefault("context_inputs", cleaner_output.get("context_inputs", {}))
    if context_format_version == "obsidian-md-v2":
        atomic_write_text(context_path, render_obsidian_markdown(snapshot, metadata=metadata_payload, cleaner_output=cleaner_output))
    else:
        atomic_write_text(context_path, render_archive_markdown(snapshot, archived_images=archived_images))
    atomic_write_text(
        metadata_path,
        render_thread_metadata(
            snapshot,
            relative_context,
            archived_images=archived_images,
            non_export_images=non_export_images,
            shared_images=shared_images,
            skipped_image_urls=skipped_image_urls,
            missing_image_urls=missing_image_urls,
            missing_shared_image_urls=missing_shared_image_urls,
            context_format_version=context_format_version,
            cleaner_version=metadata_payload.get("cleaner_version"),
            context_source_hash=metadata_payload.get("context_source_hash"),
            context_rendered_at=metadata_payload.get("context_rendered_at"),
            context_inputs=metadata_payload.get("context_inputs"),
        ),
    )
    return context_path, metadata_path
