from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from yamibo_mcp.time_utils import utc_now_iso
from yamibo_mcp.server.resource_uris import (
    job_status_uri,
    series_chapters_uri,
    series_index_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_export_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_assets_uri,
    thread_summary_uri,
    thread_update_check_uri,
)
from yamibo_mcp.yamibo.urls import thread_url_from_tid


_TERMINAL_JOB_STATUSES = {"succeeded", "partial", "failed", "cancelled", "superseded"}
_RESULT_READY_STATUSES = {"succeeded", "partial"}
_ACTIVE_JOB_STATUSES = {"queued", "running", "retrying", "interrupted", "cancel_requested", "paused"}
_USER_ACTION_ERROR_CODES = {
    "REMOTE_LOGIN_REQUIRED",
    "REMOTE_THREAD_PERMISSION_REQUIRED",
    "REMOTE_ACCESS_PAUSED",
    "GROUP_ACCESS_DENIED",
    "INVALID_ARGUMENT",
    "EXPORT_PRECHECK_FAILED",
    "LOCAL_ARCHIVE_NOT_FOUND",
    "UNSUPPORTED_DATABASE_BACKEND",
}
_AUTO_RECOVERY_ERROR_CODES = {
    "REMOTE_MAINTENANCE",
    "REMOTE_SOFT_BLOCK",
    "HTTP_444",
}


def _parse_iso8601(value: datetime | str | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _seconds_between(later: datetime | None, earlier: datetime | None) -> int | None:
    if later is None or earlier is None:
        return None
    return max(0, int((later - earlier).total_seconds()))


def _job_stopped_reason(job) -> str | None:
    artifacts = job.artifacts or {}
    if not isinstance(artifacts, dict):
        return None
    reason = artifacts.get("stopped_reason")
    if reason:
        return str(reason)
    reason = artifacts.get("download_stopped_reason")
    if reason:
        return str(reason)
    reason = artifacts.get("fetch_stopped_reason")
    if reason:
        return str(reason)
    return None


def _job_execution_diagnostics(job) -> dict[str, Any]:
    now = _parse_iso8601(utc_now_iso())
    created_at = _parse_iso8601(job.created_at)
    updated_at = _parse_iso8601(job.updated_at)
    running_duration_seconds = _seconds_between(now, created_at)
    seconds_since_update = _seconds_between(now, updated_at)

    if job.status in _TERMINAL_JOB_STATUSES:
        stopped_reason = _job_stopped_reason(job)
        if job.status == "partial":
            if stopped_reason == "stage_timeout":
                summary = "Job completed partially because download_images timed out; archived content may be incomplete and should be checked."
            elif stopped_reason:
                summary = f"Job completed partially; stopped_reason={stopped_reason}. Archived content is usually readable, but diagnostics and events should be checked."
            else:
                summary = "Job completed partially; archived content is usually readable, but diagnostics and events should be checked."
        elif job.status == "failed":
            summary = "Job failed; inspect job events before retrying."
        elif job.status == "superseded":
            summary = "Job was superseded by a rerun and is kept for history only."
        elif job.status == "cancelled":
            summary = "Job was cancelled before completion."
        else:
            summary = "Job completed successfully."
        return {
            "running_duration_seconds": running_duration_seconds,
            "seconds_since_update": seconds_since_update,
            "execution_state": "terminal",
            "diagnostic_summary": summary,
            "needs_attention": job.status in {"partial", "failed", "cancelled"} or stopped_reason == "stage_timeout",
            "recommended_poll_after_seconds": None,
        }

    if job.status == "interrupted":
        return {
            "running_duration_seconds": running_duration_seconds,
            "seconds_since_update": seconds_since_update,
            "execution_state": "attention",
            "diagnostic_summary": "Job is interrupted and waiting for daemon recovery or operator action.",
            "needs_attention": True,
            "recommended_poll_after_seconds": 5,
        }

    if job.status == "queued":
        if (running_duration_seconds or 0) >= 30:
            return {
                "running_duration_seconds": running_duration_seconds,
                "seconds_since_update": seconds_since_update,
                "execution_state": "attention",
                "diagnostic_summary": "Job has remained queued for an extended period; verify that the daemon is running.",
                "needs_attention": True,
                "recommended_poll_after_seconds": 10,
            }
        return {
            "running_duration_seconds": running_duration_seconds,
            "seconds_since_update": seconds_since_update,
            "execution_state": "queued",
            "diagnostic_summary": "Job is queued and waiting for a daemon worker to acquire it.",
            "needs_attention": False,
            "recommended_poll_after_seconds": 2,
        }

    if job.status == "paused":
        return {
            "running_duration_seconds": running_duration_seconds,
            "seconds_since_update": seconds_since_update,
            "execution_state": "paused",
            "diagnostic_summary": "Job is paused and will not be acquired by a daemon worker until resumed.",
            "needs_attention": False,
            "recommended_poll_after_seconds": 10,
        }

    if (seconds_since_update or 0) >= 120:
        return {
            "running_duration_seconds": running_duration_seconds,
            "seconds_since_update": seconds_since_update,
            "execution_state": "stalled",
            "diagnostic_summary": "Job is still running but has no progress update for over 120 seconds; inspect job events.",
            "needs_attention": True,
            "recommended_poll_after_seconds": 10,
        }

    if job.stage == "download_images" and (running_duration_seconds or 0) >= 180:
        return {
            "running_duration_seconds": running_duration_seconds,
            "seconds_since_update": seconds_since_update,
            "execution_state": "attention",
            "diagnostic_summary": "Job is spending a long time in download_images; remote images may be slow or retrying.",
            "needs_attention": True,
            "recommended_poll_after_seconds": 5,
        }

    if job.status in _ACTIVE_JOB_STATUSES:
        return {
            "running_duration_seconds": running_duration_seconds,
            "seconds_since_update": seconds_since_update,
            "execution_state": "normal",
            "diagnostic_summary": "Job is actively progressing.",
            "needs_attention": False,
            "recommended_poll_after_seconds": 2,
        }

    return {
        "running_duration_seconds": running_duration_seconds,
        "seconds_since_update": seconds_since_update,
        "execution_state": "unknown",
        "diagnostic_summary": "Job state is not recognized; inspect job events.",
        "needs_attention": True,
        "recommended_poll_after_seconds": 10,
    }


def _action(tool: str, job_id: str, reason: str) -> dict[str, Any]:
    return {"tool": tool, "args": {"job_id": job_id}, "reason": reason}


def _job_recovery(job, diagnostics: dict[str, Any]) -> dict[str, Any]:
    poll_after = diagnostics.get("recommended_poll_after_seconds")
    base = {
        "reason_code": job.error_code or job.status,
        "poll_after_seconds": poll_after,
        "next_actions": [],
    }
    if job.status == "succeeded":
        return {**base, "classification": "completed", "owner": "system", "retryable": False, "requires_user_action": False, "message": "Job completed successfully; use the result resource declared by the creating tool."}
    if job.status == "partial":
        return {**base, "classification": "use_partial_result", "owner": "agent", "retryable": False, "requires_user_action": False, "message": "Partial result is available; report incompleteness before considering a rerun.", "next_actions": [_action("read_job_events", job.job_id, "Inspect which stage produced the partial result.")]}
    if job.status in {"queued", "running", "interrupted", "cancel_requesting", "cancel_requested"}:
        return {**base, "classification": "continue_waiting", "owner": "daemon", "retryable": True, "requires_user_action": False, "message": "Job is still managed by the daemon; poll the same job_id instead of creating a duplicate.", "next_actions": [_action("read_job", job.job_id, "Poll the existing Job after the recommended delay.")]}
    if job.status == "retrying":
        return {**base, "classification": "automatic_retry", "owner": "daemon", "retryable": True, "requires_user_action": False, "message": "Daemon scheduled a delayed retry; do not create a duplicate Job.", "next_actions": [_action("read_job", job.job_id, "Wait until next_retry_at or poll_after_seconds, then re-check the same Job.")]}
    if job.status == "paused" and (job.error_code in _AUTO_RECOVERY_ERROR_CODES or not job.error_code):
        return {**base, "classification": "automatic_recovery", "owner": "daemon", "retryable": True, "requires_user_action": False, "message": "Daemon is probing remote access recovery; do not create a duplicate Job.", "next_actions": [_action("read_job", job.job_id, "Wait for the daemon recovery probe, then re-check the same Job.")]}
    if job.status == "failed" and job.error_code in _USER_ACTION_ERROR_CODES:
        return {**base, "classification": "user_action_required", "owner": "user", "retryable": False, "requires_user_action": True, "message": "The failure requires user action such as cookies, permissions, configuration, or a valid request; do not retry automatically."}
    if job.status == "failed":
        return {**base, "classification": "inspect_failure", "owner": "agent", "retryable": False, "requires_user_action": False, "message": "No deterministic recovery path is known; inspect events before deciding next steps.", "next_actions": [_action("read_job_events", job.job_id, "Read failure events before considering any new Job.")]}
    if job.status in {"cancelled", "superseded"}:
        return {**base, "classification": "stopped", "owner": "system", "retryable": False, "requires_user_action": False, "message": "Job is stopped and should not be retried as the original Job."}
    return {**base, "classification": "inspect_failure", "owner": "agent", "retryable": False, "requires_user_action": False, "message": "Job state is not recognized; inspect events.", "next_actions": [_action("read_job_events", job.job_id, "Read events for the unrecognized Job state.")]}


def job_status_payload(job) -> dict[str, Any]:
    is_terminal = job.status in _TERMINAL_JOB_STATUSES
    diagnostics = _job_execution_diagnostics(job)
    next_retry_at = job.lease_until if job.status == "retrying" else None
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
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "next_retry_at": next_retry_at,
        "artifacts": job.artifacts,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
        "is_terminal": is_terminal,
        "result_ready": job.status in _RESULT_READY_STATUSES,
        "running_duration_seconds": diagnostics["running_duration_seconds"],
        "seconds_since_update": diagnostics["seconds_since_update"],
        "execution_state": diagnostics["execution_state"],
        "diagnostic_summary": diagnostics["diagnostic_summary"],
        "needs_attention": diagnostics["needs_attention"],
        "recommended_poll_after_seconds": diagnostics["recommended_poll_after_seconds"],
        "recovery": _job_recovery(job, diagnostics),
        "resources": {
            "status": job_status_uri(job.job_id),
        },
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
        "update_check": thread_update_check_uri(tid),
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
        "forum_id": row_get("forum_id"),
        "content_kind": row_get("content_kind"),
        "category": row_get("category"),
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
