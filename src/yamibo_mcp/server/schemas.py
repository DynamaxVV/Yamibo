from __future__ import annotations

from pathlib import Path
from typing import Any

from yamibo_mcp.server.resources import (
    series_chapters_uri,
    series_index_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_export_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_assets_uri,
    thread_summary_uri,
)
from yamibo_mcp.yamibo.urls import thread_url_from_tid


def job_status_payload(job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "stage": job.stage,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "worker_id": job.worker_id,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "artifacts": job.artifacts,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


def build_series_summary(series_id: int, *, canonical_title: str | None = None) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "canonical_title": canonical_title,
        "resources": {
            "index": series_index_uri(),
            "chapters": series_chapters_uri(series_id),
        },
    }


def thread_summary_payload(row, *, include_export: bool = True) -> dict[str, Any]:
    def row_get(key: str) -> Any:
        return row[key] if key in row.keys() else None

    # 不同查询 SQL 可能只返回部分列，这里统一做成宽容读取，避免 Server 层和 SQL 强耦合。
    tid = int(row["tid"])
    export_path = row_get("export_path")
    resources = {
        "context": thread_context_uri(tid),
        "metadata": thread_metadata_uri(tid),
        "summary": thread_summary_uri(tid),
        "diagnostics": thread_diagnostics_uri(tid),
        "posts": thread_posts_uri(tid),
        "assets": thread_assets_uri(tid),
    }
    if include_export and export_path:
        resources["export"] = thread_export_uri(tid)
    return {
        "tid": tid,
        "url": thread_url_from_tid(tid),
        "display_title": row["display_title"] or row["raw_title"],
        "raw_title": row["raw_title"],
        "publisher": row_get("publisher"),
        "pub_time": row_get("pub_time"),
        "core_title": row_get("core_title_guess"),
        "chapter_name": row_get("chapter_name"),
        "chapter_title": row_get("chapter_title"),
        "series_id": row_get("series_id"),
        "series_key": row_get("series_key"),
        "archive_status": row_get("archive_status"),
        "validation_status": row_get("validation_status"),
        "sync_time": row_get("sync_time"),
        "export_path": export_path,
        "resources": resources,
    }


def thread_detail_payload(thread_row, title_row, floors, *, data_dir: Path) -> dict[str, Any]:
    export_path_value = thread_row["export_path"]
    if export_path_value and Path(str(export_path_value)).is_absolute():
        export_path_abs = str(export_path_value)
    else:
        export_path_abs = str(data_dir / export_path_value) if export_path_value else None
    summary = thread_summary_payload(thread_row)
    if title_row is not None:
        if summary.get("core_title") is None:
            summary["core_title"] = title_row["core_title_guess"]
        if summary.get("chapter_name") is None:
            summary["chapter_name"] = title_row["chapter_name"]
        if summary.get("chapter_title") is None:
            summary["chapter_title"] = title_row["chapter_title"]
        if summary.get("series_key") is None:
            summary["series_key"] = title_row["series_key"]
    summary["publisher"] = thread_row["publisher"]
    summary["publisher_uid"] = thread_row["publisher_uid"]
    summary["pub_time"] = thread_row["pub_time"]
    summary["image_count"] = thread_row["image_count"]
    summary["context_path"] = str(data_dir / thread_row["context_path"]) if thread_row["context_path"] else None
    summary["export_path_abs"] = export_path_abs
    summary["title_parse"] = None if title_row is None else {
        "group_name": title_row["group_name"],
        "author_guess": title_row["author_guess"],
        "core_title_guess": title_row["core_title_guess"],
        "normalized_core_title": title_row["normalized_core_title"],
        "series_key": title_row["series_key"],
        "title_aliases_json": title_row["title_aliases_json"],
        "chapter_name": title_row["chapter_name"],
        "chapter_index": title_row["chapter_index"],
        "chapter_index_end": title_row["chapter_index_end"],
        "chapter_title": title_row["chapter_title"],
        "subtitle": title_row["subtitle"],
        "tags_json": title_row["tags_json"],
        "confidence": title_row["confidence"],
        "needs_review": title_row["needs_review"],
    }
    summary["floors"] = [
        {
            "pid": floor["pid"],
            "floor_no": floor["floor_no"],
            "publisher": floor["publisher"],
            "pub_time": floor["pub_time"],
            "has_images": bool(floor["has_images"]),
            "content": floor["content"] or "",
            "content_preview": (floor["content"] or "")[:280],
        }
        for floor in floors
    ]
    summary["floor_count"] = len(floors)
    return summary
