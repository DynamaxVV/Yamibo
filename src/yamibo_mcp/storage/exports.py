from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yamibo_mcp.storage.paths import StoragePaths


class ExportPrecheckError(ValueError):
    pass


@dataclass(frozen=True)
class ExportReadiness:
    context_exists: bool
    metadata_exists: bool
    images_complete: bool
    missing_image_count: int
    missing_files: list[str]


def inspect_export_readiness(paths: StoragePaths, tid: int) -> ExportReadiness:
    thread_dir = paths.thread_dir(tid)
    context_path = paths.thread_context(tid)
    metadata_path = paths.thread_metadata(tid)
    if not thread_dir.exists():
        raise ExportPrecheckError(f"thread archive directory not found: {thread_dir}")
    context_exists = context_path.exists()
    metadata_exists = metadata_path.exists()
    if not context_exists:
        raise ExportPrecheckError(f"context markdown not found: {context_path}")
    if not metadata_exists:
        raise ExportPrecheckError(f"metadata json not found: {metadata_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    missing_image_urls = metadata.get("missing_image_urls") or []
    archived_images = metadata.get("archived_images") or {}
    missing_files: list[str] = []
    for relpaths in archived_images.values():
        for relpath in relpaths:
            relative_path = str(relpath)
            file_path = thread_dir / relative_path
            if not file_path.exists():
                missing_files.append(str(relpath))
    return ExportReadiness(
        context_exists=context_exists,
        metadata_exists=metadata_exists,
        images_complete=not missing_image_urls and not missing_files,
        missing_image_count=len(missing_image_urls),
        missing_files=missing_files,
    )


def is_thread_stale(sync_time: str | None, *, stale_after_hours: int) -> bool:
    if not sync_time:
        return True
    try:
        normalized = sync_time.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)


def export_thread_zip(
    paths: StoragePaths,
    tid: int,
    *,
    series_name: str | None = None,
    chapter_name: str | None = None,
    chapter_title: str | None = None,
    display_title: str | None = None,
) -> Path:
    thread_dir = paths.thread_dir(tid)
    readiness = inspect_export_readiness(paths, tid)
    if not readiness.images_complete:
        raise ExportPrecheckError(
            f"thread archive is missing images or image files: missing_urls={readiness.missing_image_count}, missing_files={len(readiness.missing_files)}"
        )

    series_dirname = _safe_export_segment(series_name or "未归类系列")
    zip_basename = _build_zip_basename(
        tid,
        chapter_name=chapter_name,
        chapter_title=chapter_title,
        display_title=display_title,
    )
    export_path = paths.thread_export_zip(tid, series_dirname=series_dirname, zip_basename=zip_basename)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_directory_writable(export_path.parent)
    tmp_path = export_path.with_suffix(export_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        metadata = json.loads(paths.thread_metadata(tid).read_text(encoding="utf-8"))
        excluded_relpaths = {
            str(relpath)
            for relpaths in (metadata.get("non_export_images") or {}).values()
            for relpath in relpaths
            if isinstance(relpath, str)
        }
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(thread_dir.rglob("*")):
                if path.is_file():
                    rel_from_thread = str(path.relative_to(thread_dir))
                    if rel_from_thread in excluded_relpaths:
                        continue
                    zf.write(path, arcname=str(path.relative_to(thread_dir.parent)))

        _verify_zip(tmp_path)
        tmp_path.replace(export_path)
        return export_path
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _verify_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise ExportPrecheckError(f"zip verification failed at {bad_file}")
        names = set(zf.namelist())
        if not any(name.endswith("/context.md") for name in names):
            raise ExportPrecheckError("zip missing context.md")
        if not any(name.endswith("/metadata.json") for name in names):
            raise ExportPrecheckError("zip missing metadata.json")


def _ensure_directory_writable(path: Path) -> None:
    if not path.exists():
        raise ExportPrecheckError(f"export directory not found: {path}")
    if not path.is_dir():
        raise ExportPrecheckError(f"export path is not a directory: {path}")
    if not os.access(path, os.W_OK | os.X_OK):
        raise ExportPrecheckError(f"export directory is not writable: {path}")


def _build_zip_basename(
    tid: int,
    *,
    chapter_name: str | None,
    chapter_title: str | None,
    display_title: str | None,
) -> str:
    chapter_label = None
    if chapter_name and chapter_title:
        chapter_label = f"{chapter_name}-{chapter_title}"
    elif chapter_name:
        chapter_label = chapter_name
    else:
        chapter_label = chapter_title
    preferred = chapter_label or display_title or f"thread_{tid}"
    return f"{_safe_export_segment(preferred)}.zip"


def _safe_export_segment(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value[:120].strip()
    return value or "untitled"
