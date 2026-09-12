from __future__ import annotations

import json

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.forums import resolve_forum
from yamibo_mcp.domain.models import Job
from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.storage.exports import (
    ExportPrecheckError,
    export_thread_txt,
    export_thread_zip,
    inspect_export_readiness,
    is_thread_stale,
)
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.daemon.handlers.sync_thread import handle_sync_thread
from yamibo_mcp.daemon.handlers.sync_thread import JobCancelled, _check_cancelled
from yamibo_mcp.yamibo.urls import thread_url_from_tid


_EXPORT_IMAGE_BACKFILL_DELAY_SECONDS = 5
_LIVE_IMAGE_BACKFILL_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.INTERRUPTED.value,
}


def handle_export_thread(repo: JobsRepository, job: Job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
    _check_cancelled(repo, job.job_id)
    tid = job.tid or job.payload.get("tid")
    if tid is None:
        raise ValueError("export_thread requires tid")
    tid = int(tid)
    strategy = str(job.payload.get("strategy") or settings.export_default_strategy)
    if strategy not in {"cache_only", "sync_if_stale", "force_resync"}:
        raise ValueError(f"unsupported export strategy: {strategy}")

    repo.update_stage(job.job_id, "precheck", progress_current=1, progress_total=4)
    repo.heartbeat(job.job_id, worker_id, lease_seconds)
    thread_repo = ThreadsRepository(repo.conn)
    thread = thread_repo.get_thread(tid)
    sync_job_info: dict[str, object] | None = None
    paths = StoragePaths(
        settings.data_dir,
        export_dir=settings.export_dir,
        novel_txt_export_dir=settings.novel_txt_export_dir,
    )

    should_resync = strategy == "force_resync"
    if thread is None:
        should_resync = strategy in {"sync_if_stale", "force_resync"}
        if strategy == "cache_only":
            raise ValueError(f"thread not found locally for cache_only export: {tid}")

    if thread is not None and not should_resync:
        try:
            readiness = inspect_export_readiness(paths, tid)
        except ExportPrecheckError:
            readiness = None
        if strategy == "sync_if_stale":
            should_resync = (
                thread["archive_status"] != "complete"
                or readiness is None
                or not readiness.images_complete
                or is_thread_stale(thread["sync_time"], stale_after_hours=settings.export_stale_after_hours)
            )

    if should_resync:
        sync_job = repo.create(
            JobType.SYNC_THREAD.value,
            tid=tid,
            payload={
                key: value
                for key, value in {
                    "tid": tid,
                    "url": job.payload.get("url"),
                    "base_url": job.payload.get("base_url"),
                }.items()
                if value is not None
            },
            parent_job_id=job.job_id,
        )
        sync_job = repo.acquire(sync_job.job_id, f"{worker_id}_sync", lease_seconds)
        try:
            handle_sync_thread(repo, sync_job, f"{worker_id}_sync", lease_seconds, settings)
        except Exception as exc:  # noqa: BLE001
            repo.fail(sync_job.job_id, exc.__class__.__name__, str(exc))
            raise
        sync_job_info = {"job_id": sync_job.job_id}
        thread = thread_repo.get_thread(tid)

    if thread is None:
        raise ValueError(f"thread not found after export precheck: {tid}")
    readiness_error: ExportPrecheckError | None = None
    try:
        readiness = inspect_export_readiness(paths, tid)
    except ExportPrecheckError as exc:
        readiness = None
        readiness_error = exc

    archive_status = str(thread["archive_status"] or "")
    images_incomplete = (
        archive_status == "partial"
        or readiness is None
        or not readiness.images_complete
        or not readiness.first_floor_complete
    )
    if images_incomplete and strategy != "cache_only" and archive_status in {"", "partial", "complete", "stale"}:
        _wait_for_export_image_backfill(
            repo,
            job,
            tid=tid,
            readiness=readiness,
            readiness_error=readiness_error,
        )
        return

    if archive_status != "complete":
        missing_urls = _thread_missing_image_urls(thread)
        if archive_status == "partial":
            preview = ", ".join(missing_urls[:2])
            detail = f"; missing_image_count={len(missing_urls)}"
            if preview:
                detail += f"; missing_image_urls={preview}"
            raise ValueError(f"thread archive is partial: {tid}{detail}")
        raise ValueError(f"thread archive is not complete: {tid}; archive_status={archive_status}")
    if readiness_error is not None:
        raise readiness_error
    is_novel = thread["content_kind"] == "novel" or thread["forum_id"] == 55
    if not is_novel and (not readiness.images_complete or not readiness.first_floor_complete):
        raise ExportPrecheckError(
            "thread archive images are incomplete: "
            f"missing_urls={readiness.missing_image_count}, "
            f"missing_files={len(readiness.missing_files)}, "
            f"first_floor_missing={readiness.first_floor_missing_image_count}"
        )

    repo.update_stage(job.job_id, "export_write", progress_current=2, progress_total=4)
    title = thread_repo.get_title_parse(tid)
    if is_novel:
        forum_name = resolve_forum(thread["forum_id"]).name if thread["forum_id"] is not None else "轻小说区"
        txt_result = export_thread_txt(
            paths,
            tid,
            title=(thread["display_title"] or thread["raw_title"]),
            source_url=thread_url_from_tid(tid),
            forum_name=forum_name,
            translator=thread["publisher"],
            include_filtered_notes=settings.novel_txt_include_filtered_notes,
            debug_markers=settings.novel_txt_debug_markers,
        )
        export_path = txt_result.export_path
    else:
        export_path = export_thread_zip(
            paths,
            tid,
            series_name=None if title is None else (title["series_key"] or title["core_title_guess"]),
            chapter_name=None if title is None else title["chapter_name"],
            chapter_title=None if title is None else title["chapter_title"],
            display_title=thread["display_title"] or thread["raw_title"],
        )
    try:
        relative_export = str(export_path.relative_to(settings.data_dir))
    except ValueError:
        relative_export = str(export_path)

    repo.update_stage(job.job_id, "db_commit", progress_current=3, progress_total=4)
    with transaction(repo.conn):
        ThreadsRepository(repo.conn).mark_exported(tid, relative_export)

    repo.update_stage(job.job_id, "finalize", progress_current=4, progress_total=4)
    artifacts = {
        "tid": tid,
        "strategy": strategy,
        "export_path": str(export_path),
        "relative_export_path": relative_export,
        "sync_job": sync_job_info,
        "export_format": "txt" if is_novel else "zip",
    }
    if is_novel:
        artifacts["manifest_path"] = str(txt_result.manifest_path)
        artifacts["appended_floors"] = txt_result.appended_floors
        artifacts["filtered_floors"] = txt_result.filtered_floors
        artifacts["needs_full_regenerate"] = txt_result.needs_full_regenerate
    repo.succeed(job.job_id, artifacts)


def _wait_for_export_image_backfill(
    repo: JobsRepository,
    job: Job,
    *,
    tid: int,
    readiness,
    readiness_error: ExportPrecheckError | None,
) -> None:
    """Queue missing-image repair and keep the export job pending until it is ready."""
    artifacts: dict[str, object] = {
        "export_wait": {
            "reason": "missing_images",
            "include_first_floor": True,
            "first_floor_missing_image_count": (
                readiness.first_floor_missing_image_count if readiness is not None else None
            ),
            "missing_image_count": readiness.missing_image_count if readiness is not None else None,
            "missing_file_count": len(readiness.missing_files) if readiness is not None else None,
            "readiness_error": str(readiness_error) if readiness_error is not None else None,
        }
    }

    latest_child = _latest_image_backfill_child(repo, job.job_id)
    backfill_job = None
    if (
        latest_child is not None
        and latest_child.job_type == JobType.IMAGE_BACKFILL.value
        and latest_child.status in _LIVE_IMAGE_BACKFILL_STATUSES
    ):
        backfill_job = latest_child

    if backfill_job is None and job.retry_count < job.max_retries:
        backfill_payload = {
            "tid": tid,
            "mode": "reconcile_missing",
            "scope": "selected",
            "include_first_floor": True,
            "dry_run": False,
            "campaign": "export_missing_images",
            "export_job_id": job.job_id,
            "transport_preference": "direct",
        }
        if job.payload.get("base_url") is not None:
            backfill_payload["base_url"] = job.payload["base_url"]
        backfill_job = repo.create(
            JobType.IMAGE_BACKFILL.value,
            tid=tid,
            payload=backfill_payload,
            parent_job_id=job.job_id,
        )

    if backfill_job is not None:
        artifacts["image_backfill_job"] = {
            "job_id": backfill_job.job_id,
            "status": backfill_job.status,
            "mode": backfill_job.payload.get("mode"),
            "include_first_floor": bool(backfill_job.payload.get("include_first_floor")),
        }

    message = (
        f"export waits for image backfill: tid={tid}; "
        f"first_floor_missing={artifacts['export_wait']['first_floor_missing_image_count']}"
    )
    if repo.retry_later(
        job.job_id,
        error_code="EXPORT_PRECHECK_FAILED",
        error_message=message,
        artifacts=artifacts,
        delay_seconds=_EXPORT_IMAGE_BACKFILL_DELAY_SECONDS,
    ):
        return

    repo.fail(
        job.job_id,
        "EXPORT_PRECHECK_FAILED",
        f"image backfill did not make the archive exportable: tid={tid}",
        artifacts=artifacts,
    )


def _latest_image_backfill_child(repo: JobsRepository, parent_job_id: str) -> Job | None:
    """Find the latest image child without being confused by same-second job timestamps."""
    row = repo.conn.execute(
        """
        SELECT job_id
        FROM jobs
        WHERE parent_job_id = ? AND job_type = ?
        ORDER BY created_at DESC, job_id DESC
        LIMIT 1
        """,
        (parent_job_id, JobType.IMAGE_BACKFILL.value),
    ).fetchone()
    return None if row is None else repo.get(str(row["job_id"]))


def _thread_missing_image_urls(thread) -> list[str]:
    """Read JSONB values from either PostgreSQL's decoded list or SQLite JSON text."""
    value = thread["missing_images_json"] if "missing_images_json" in thread.keys() else None
    if isinstance(value, (list, tuple)):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            parsed = []
    else:
        parsed = []
    if not isinstance(parsed, (list, tuple)):
        return []
    return [str(item) for item in parsed if str(item).strip()]
