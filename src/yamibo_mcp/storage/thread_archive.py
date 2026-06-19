from __future__ import annotations

from pathlib import Path

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.images import materialize_staged_images
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.markdown import render_thread_markdown, render_thread_metadata
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
) -> tuple[Path, Path]:
    context_path = paths.thread_context(snapshot.tid)
    metadata_path = paths.thread_metadata(snapshot.tid)
    relative_context = str(context_path.relative_to(paths.data_dir))
    if job_id:
        materialize_staged_images(paths, job_id, snapshot.tid)
    atomic_write_text(context_path, render_thread_markdown(snapshot, archived_images=archived_images))
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
        ),
    )
    return context_path, metadata_path
