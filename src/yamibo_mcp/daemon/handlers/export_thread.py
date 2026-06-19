from __future__ import annotations

import json

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import Job
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.storage.exports import ExportPrecheckError, export_thread_zip, inspect_export_readiness, is_thread_stale
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.daemon.handlers.sync_thread import handle_sync_thread


def handle_export_thread(repo: JobsRepository, job: Job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
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
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir)

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
    if thread["archive_status"] != "complete":
        missing_urls = json.loads(thread["missing_images_json"] or "[]")
        if thread["archive_status"] == "partial":
            preview = ", ".join(missing_urls[:2])
            detail = f"; missing_image_count={len(missing_urls)}"
            if preview:
                detail += f"; missing_image_urls={preview}"
            raise ValueError(f"thread archive is partial: {tid}{detail}")
        raise ValueError(f"thread archive is not complete: {tid}; archive_status={thread['archive_status']}")
    readiness = inspect_export_readiness(paths, tid)
    if not readiness.images_complete:
        raise ExportPrecheckError(
            f"thread archive images are incomplete: missing_urls={readiness.missing_image_count}, missing_files={len(readiness.missing_files)}"
        )

    repo.update_stage(job.job_id, "zip_write", progress_current=2, progress_total=4)
    title = thread_repo.get_title_parse(tid)
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
    repo.succeed(
        job.job_id,
        {
            "tid": tid,
            "strategy": strategy,
            "export_path": str(export_path),
            "relative_export_path": relative_export,
            "sync_job": sync_job_info,
        },
    )
