from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.yamibo.cleaners.content_cleaner import clean_content


class ExportPrecheckError(ValueError):
    pass


@dataclass(frozen=True)
class ExportReadiness:
    context_exists: bool
    metadata_exists: bool
    images_complete: bool
    missing_image_count: int
    missing_files: list[str]
    first_floor_complete: bool = True
    first_floor_missing_image_count: int = 0


@dataclass(frozen=True)
class NovelTxtExportResult:
    export_path: Path
    manifest_path: Path
    appended_floors: int
    filtered_floors: int
    needs_full_regenerate: bool


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
    missing_image_urls = _unique_strings(
        [
            *(metadata.get("missing_image_urls") or []),
            *(metadata.get("missing_shared_image_urls") or []),
        ]
    )
    archived_images = metadata.get("archived_images") or {}
    missing_files: list[str] = []
    for relpaths in archived_images.values():
        for relpath in relpaths:
            relative_path = str(relpath)
            file_path = thread_dir / relative_path
            if not file_path.exists():
                missing_files.append(str(relpath))
    first_floor_complete, first_floor_missing_image_count = _inspect_first_floor_readiness(
        paths,
        tid,
        metadata,
    )
    return ExportReadiness(
        context_exists=context_exists,
        metadata_exists=metadata_exists,
        images_complete=not missing_image_urls and not missing_files,
        missing_image_count=len(missing_image_urls),
        missing_files=missing_files,
        first_floor_complete=first_floor_complete,
        first_floor_missing_image_count=first_floor_missing_image_count,
    )


def _inspect_first_floor_readiness(
    paths: StoragePaths,
    tid: int,
    metadata: dict[str, object],
) -> tuple[bool, int]:
    """Check every materialized image slot on the comic's first floor.

    The first floor is the primary comic payload.  Its ``image_slots`` retain
    the remote URL-to-local-path mapping, so checking only the thread-level
    ``missing_image_urls`` list is not sufficient for older or partially
    materialized metadata.
    """
    missing_global = set(
        _unique_strings(
            [
                *(metadata.get("missing_image_urls") or []),
                *(metadata.get("missing_shared_image_urls") or []),
            ]
        )
    )
    missing_urls: set[str] = set()
    floors = metadata.get("floors") or []
    for floor in floors:
        if not isinstance(floor, dict):
            continue
        try:
            floor_no = int(floor.get("floor_no"))
        except (TypeError, ValueError):
            continue
        if floor_no != 1:
            continue

        remote_urls = _unique_strings(
            [*(floor.get("remote_image_urls") or [])]
        )
        slots = floor.get("image_slots") or []
        slot_urls: set[str] = set()
        if isinstance(slots, list) and slots:
            for raw_slot in slots:
                if not isinstance(raw_slot, dict):
                    continue
                remote_url = str(raw_slot.get("remote_url") or "").strip()
                if not remote_url:
                    continue
                slot_urls.add(remote_url)
                status = str(raw_slot.get("status") or "").strip().lower()
                if status == "skipped":
                    continue
                local_path = str(raw_slot.get("local_path") or "").strip()
                if status in {"missing", "missing_shared", "pending"} or not local_path:
                    missing_urls.add(remote_url)
                    continue
                if not _metadata_image_path(paths, tid, local_path).is_file():
                    missing_urls.add(remote_url)

        # Older metadata may not contain image_slots.  In that case the
        # thread-level missing lists are the only reliable URL-level signal.
        missing_urls.update(url for url in remote_urls if url in missing_global and url not in slot_urls)

    return not missing_urls, len(missing_urls)


def _metadata_image_path(paths: StoragePaths, tid: int, local_path: str) -> Path:
    path = Path(local_path)
    if path.is_absolute():
        return path
    if str(path).startswith("shared/"):
        return paths.data_dir / path
    return paths.thread_dir(tid) / path


def _unique_strings(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def is_thread_stale(sync_time: str | datetime | None, *, stale_after_hours: int) -> bool:
    if not sync_time:
        return True
    if isinstance(sync_time, datetime):
        dt = sync_time
    else:
        try:
            normalized = str(sync_time).replace("Z", "+00:00")
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


def export_thread_txt(
    paths: StoragePaths,
    tid: int,
    *,
    title: str,
    source_url: str | None,
    forum_name: str | None,
    translator: str | None,
    include_filtered_notes: bool = False,
    debug_markers: bool = False,
) -> NovelTxtExportResult:
    inspect_export_readiness(paths, tid)
    metadata = json.loads(paths.thread_metadata(tid).read_text(encoding="utf-8"))
    floors = metadata.get("floors") or []
    if not floors:
        raise ExportPrecheckError(f"thread archive has no floors to export: {tid}")

    txt_basename = _build_txt_basename(tid, title=title)
    export_path = paths.thread_export_txt(tid, txt_basename=txt_basename)
    manifest_path = paths.thread_export_txt_manifest(tid, txt_basename=txt_basename)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_directory_writable(export_path.parent)

    manifest = _load_manifest(manifest_path)
    exported_by_pid = {
        int(item["pid"]): str(item["content_hash"])
        for item in manifest.get("exported_pids", [])
        if "pid" in item and "content_hash" in item
    }

    filtered_records: list[dict[str, object]] = []
    exportable_records: list[dict[str, object]] = []
    for floor in floors:
        content = clean_content(str(floor.get("content") or ""))
        kind, reasons = _classify_novel_floor_for_export(
            content,
            floor_no=int(floor.get("floor_no") or 0),
            is_first_floor=int(floor.get("floor_no") or 0) == 1,
        )
        record = {
            "pid": int(floor["pid"]),
            "floor_no": int(floor["floor_no"]),
            "kind": kind,
            "content": content,
            "content_hash": _content_hash(content),
            "char_count": len(content),
            "reasons": reasons,
            "pub_time": floor.get("pub_time"),
        }
        if kind == "filtered":
            filtered_records.append(record)
            if include_filtered_notes:
                exportable_records.append(record | {"kind": "note"})
            continue
        exportable_records.append(record)

    existing_exported = [item for item in manifest.get("exported_pids", []) if isinstance(item, dict)]
    appended_records = [
        record
        for record in exportable_records
        if record["pid"] not in exported_by_pid
    ]
    needs_full_regenerate = any(
        exported_by_pid.get(record["pid"]) not in {None, record["content_hash"]}
        for record in exportable_records
    )

    if not export_path.exists():
        header = _render_novel_txt_header(
            title=title,
            translator=translator,
            source_url=source_url,
            forum_name=forum_name,
        )
        body = "".join(
            _render_novel_txt_record(record, debug_markers=debug_markers)
            for record in exportable_records
        )
        atomic_write_text(export_path, header + body)
        appended_records = exportable_records
        existing_exported = []
        needs_full_regenerate = False
    elif appended_records:
        with export_path.open("a", encoding="utf-8") as handle:
            for record in appended_records:
                handle.write(_render_novel_txt_record(record, debug_markers=debug_markers))

    manifest_payload = {
        "tid": tid,
        "export_format": "txt",
        "txt_path": str(export_path),
        "source_url": source_url,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exported_pids": existing_exported + [
            {
                "pid": record["pid"],
                "floor_no": record["floor_no"],
                "kind": record["kind"],
                "content_hash": record["content_hash"],
                "char_count": record["char_count"],
            }
            for record in appended_records
        ],
        "filtered_floors": [
            {
                "pid": record["pid"],
                "floor_no": record["floor_no"],
                "char_count": record["char_count"],
                "reasons": record["reasons"],
            }
            for record in filtered_records
        ],
        "needs_full_regenerate": needs_full_regenerate,
    }
    atomic_write_text(manifest_path, json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n")
    return NovelTxtExportResult(
        export_path=export_path,
        manifest_path=manifest_path,
        appended_floors=len(appended_records),
        filtered_floors=len(filtered_records),
        needs_full_regenerate=needs_full_regenerate,
    )


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


def _build_txt_basename(tid: int, *, title: str | None) -> str:
    preferred = title or f"thread_{tid}"
    return f"{_safe_export_segment(preferred)}.txt"


def _safe_export_segment(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value[:120].strip()
    return value or "untitled"


def _load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _content_hash(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _classify_novel_floor_for_export(content: str, *, floor_no: int, is_first_floor: bool) -> tuple[str, list[str]]:
    normalized = content.strip()
    if is_first_floor:
        return "info", ["first_floor"]
    chapter_signal = re.search(
        r"(^|\n)\s*(第\s*[0-9一二三四五六七八九十百千零0-9]+\s*[话章节卷篇]|chapter\s*\d+|番外|后记|终章|extra)",
        normalized,
        flags=re.IGNORECASE,
    )
    if chapter_signal:
        return "body", ["chapter_signal"]
    if len(normalized) >= 500:
        return "body", ["length_threshold"]
    reply_signal = re.search(
        r"(发表于|感谢|谢谢|更新|推荐|我看|回复|楼上|顺便|另外|说明)",
        normalized,
        flags=re.IGNORECASE,
    )
    if reply_signal:
        return "filtered", ["short_reply_like"]
    return "filtered", ["short_non_body"]


def _render_novel_txt_header(
    *,
    title: str,
    translator: str | None,
    source_url: str | None,
    forum_name: str | None,
) -> str:
    lines = [
        title.strip(),
        "",
    ]
    if translator:
        lines.append(f"译者：{translator}")
    if source_url:
        lines.append(f"来源：{source_url}")
    if forum_name:
        lines.append(f"分区：{forum_name}")
    lines.append(f"导出时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}")
    lines.extend(["", "=" * 60, "", ""])
    return "\n".join(lines)


def _render_novel_txt_record(record: dict[str, object], *, debug_markers: bool) -> str:
    content = str(record["content"]).strip()
    lines: list[str] = []
    if debug_markers:
        lines.append(
            f"--- pid:{record['pid']} floor:{record['floor_no']} kind:{record['kind']} ---"
        )
        lines.append("")
    if record["kind"] == "note":
        lines.append("[过滤备注]")
        lines.append("")
    lines.append(content)
    lines.extend(["", "", "=" * 60, "", ""])
    return "\n".join(lines)
