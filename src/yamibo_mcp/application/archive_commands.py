from __future__ import annotations

from typing import Any
from yamibo_mcp.db.transaction_scope import BorrowedConnection

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.application.update_commands import create_update_thread_job
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.domain.models import Job
from yamibo_mcp.server.resource_uris import job_events_uri, thread_summary_uri
from yamibo_mcp.yamibo.anti_bot import ensure_remote_access_allowed, is_http_444_error
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.proxy_pool import select_random_proxy
from yamibo_mcp.yamibo.urls import thread_url_from_tid


def _can_reuse_existing_job(existing_payload: dict[str, Any], requested_payload: dict[str, Any]) -> bool:
    return existing_payload == requested_payload


def _validate_archive_mode(mode: str) -> None:
    if not isinstance(mode, str) or mode not in {"text_only", "full"}:
        raise ValueError("mode must be text_only or full")


def _prepare_archive_job(
    repo: JobsRepository, payload: dict[str, Any],
) -> tuple[str | None, dict[str, Any], Job | None]:
    """Resolve archive vs upgrade before writing any jobs in a batch."""
    tid = payload.get("tid")
    mode = payload["mode"]
    thread = ThreadsRepository(repo.conn).get_thread(tid) if tid is not None else None
    if (
        thread is not None and mode == "text_only" and not payload.get("html_path")
        and thread["archive_status"] in {"complete", "partial"}
        and (thread["archive_status"] == "complete" or thread["capture_mode"] == "full")
    ):
        return None, payload, None

    job_type = JobType.SYNC_THREAD.value
    missing_images = mode == "full" and thread is not None and any(
        asset["asset_type"] == "image" and not asset["local_path"]
        for asset in AssetsRepository(repo.conn).list_assets(tid)
    )
    can_backfill = thread is not None and (
        (thread["capture_mode"] == "text_only" and thread["archive_status"] == "complete")
        or (thread["capture_mode"] == "full" and thread["archive_status"] in {"complete", "partial"} and missing_images)
    )
    if mode == "full" and not payload.get("html_path") and can_backfill:
        job_type = JobType.IMAGE_BACKFILL.value
        payload = {
            "tid": tid,
            "dry_run": False,
            "scope": "selected",
            "include_first_floor": True,
            "upgrade_to_full": True,
            **({"base_url": payload["base_url"]} if "base_url" in payload else {}),
        }
    if tid is None:
        return job_type, payload, None
    live_sync = repo.find_live_job_for_thread(job_type=JobType.SYNC_THREAD.value, tid=tid)
    live_backfill = repo.find_live_job_for_thread(job_type=JobType.IMAGE_BACKFILL.value, tid=tid)
    for live in (live_sync, live_backfill):
        if live is None:
            continue
        if live.job_type == job_type and (
            {"mode": "full", **live.payload} if job_type == JobType.SYNC_THREAD.value else live.payload
        ) == payload:
            continue
        # Image upgrades and syncs both write the thread snapshot; don't race them.
        if live.job_type == JobType.IMAGE_BACKFILL.value or job_type == JobType.IMAGE_BACKFILL.value or live.payload.get("mode", "full") != mode:
            raise ValueError(f"thread {tid} has a conflicting active archive job: {live.job_id}; wait for it before changing archive mode")
    existing = repo.find_live_job_for_thread(job_type=job_type, tid=tid, payload=payload)
    return job_type, payload, existing


def _enqueue_archive_job(
    repo: JobsRepository,
    prepared: tuple[str | None, dict[str, Any], Job | None],
    mode: str,
) -> dict[str, Any]:
    job_type, payload, existing = prepared
    if job_type is None:
        return {
            "tid": payload.get("tid"), "job_id": None, "job_type": None,
            "created": False, "requested_mode": mode, "status": "satisfied",
        }
    job = existing or repo.create(job_type, tid=payload.get("tid"), payload=payload)
    return {
        "tid": payload.get("tid"), "job_id": job.job_id, "job_type": job_type,
        "created": existing is None, "requested_mode": mode, "status": job.status,
    }


def archive_thread_job(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
    mode: str = "full",
    connection=None,
) -> dict[str, Any]:
    _validate_archive_mode(mode)
    if not html_path and not tid and not url:
        raise ValueError("archive_thread requires html_path or tid or url")
    settings = load_settings()
    conn = BorrowedConnection(connection) if connection is not None else connect(settings.db_path)
    try:
        repo = JobsRepository(conn)
        payload = {
            key: value
            for key, value in {
                "html_path": html_path,
                "tid": tid,
                "url": url,
                "base_url": base_url,
                "forum_id": forum_id,
                "mode": mode,
            }.items()
            if value is not None
        }
        prepared = _prepare_archive_job(repo, payload)
        if html_path is None and prepared[0] is not None:
            ensure_remote_access_allowed(conn)
        return _enqueue_archive_job(repo, prepared, mode)
    finally:
        conn.close()


def create_thread_archive_job(
    *,
    tid: int | None = None,
    url: str | None = None,
    html_path: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
    mode: str = "full",
    connection=None,
) -> AgentResult:
    payload = archive_thread_job(
        connection=connection,
        tid=tid,
        url=url,
        html_path=html_path,
        base_url=base_url,
        forum_id=forum_id,
        mode=mode,
    )
    job_id = payload["job_id"]
    if job_id is None:
        return AgentResult(ok=True, data=payload, resources={"summary": thread_summary_uri(tid)})
    created = bool(payload["created"])
    return AgentResult(
        ok=True,
        data=payload,
        resources={"job_events": job_events_uri(job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job_id}, reason="Poll the archive job until it finishes."),
        ],
        side_effects=["job_created" if created else "job_reused", "daemon_required"],
    )


def create_thread_archive_batch_jobs(
    *,
    tids: list[int],
    base_url: str | None = None,
    forum_id: int | None = None,
    mode: str = "full",
    connection=None,
) -> AgentResult:
    _validate_archive_mode(mode)
    normalized_tids = list(dict.fromkeys(int(tid) for tid in tids if tid))
    if not normalized_tids:
        raise ValueError("tids required")
    settings = load_settings()
    conn = BorrowedConnection(connection) if connection is not None else connect(settings.db_path)
    try:
        repo = JobsRepository(conn)
        prepared = [
            _prepare_archive_job(repo, {
                key: value for key, value in {
                    "tid": tid, "base_url": base_url, "forum_id": forum_id, "mode": mode,
                }.items() if value is not None
            })
            for tid in normalized_tids
        ]
        if any(item[0] is not None for item in prepared):
            ensure_remote_access_allowed(conn)
        jobs = [_enqueue_archive_job(repo, item, mode) for item in prepared]
    finally:
        conn.close()

    created_job_ids = [item["job_id"] for item in jobs if item["created"]]
    reused_job_ids = [item["job_id"] for item in jobs if not item["created"] and item["job_id"] is not None]
    job_types = {item["job_type"] for item in jobs if item["job_type"] is not None}
    return AgentResult(
        ok=True,
        data={
            "job_type": next(iter(job_types)) if len(job_types) == 1 else None,
            "target_count": len(normalized_tids),
            "created_count": len(created_job_ids),
            "reused_count": len(reused_job_ids),
            "satisfied_count": sum(item["status"] == "satisfied" for item in jobs),
            "created_job_ids": created_job_ids,
            "reused_job_ids": reused_job_ids,
            "tids": normalized_tids,
            "requested_mode": mode,
            "jobs": jobs,
        },
        side_effects=(["job_created" if created_job_ids else "job_reused", "daemon_required"] if created_job_ids or reused_job_ids else []),
    )


def ensure_thread_archived(
    *,
    tid: int,
    base_url: str | None = None,
    forum_id: int | None = None,
    mode: str = "full",
) -> AgentResult:
    _validate_archive_mode(mode)
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        thread = ThreadsRepository(conn).get_thread(tid)
    finally:
        conn.close()

    if thread is not None and thread["archive_status"] == "complete" and (
        mode == "text_only" or thread["capture_mode"] == "full"
    ):
        return AgentResult(
            ok=True,
            data={
                "tid": tid,
                "archived": True,
                "archive_status": thread["archive_status"],
                "capture_mode": thread["capture_mode"],
                "source": "local_archive",
            },
            resources={"summary": thread_summary_uri(tid)},
        )

    result = create_thread_archive_job(tid=tid, base_url=base_url, forum_id=forum_id, mode=mode)
    if result.data is not None:
        result.data["tid"] = tid
        result.data["archived"] = False
        result.data["source"] = "job_created"
    return result


def create_thread_export_job(*, tid: int, strategy: str | None = None, connection=None) -> AgentResult:
    settings = load_settings()
    conn = BorrowedConnection(connection) if connection is not None else connect(settings.db_path)
    try:
        thread = ThreadsRepository(conn).get_thread(tid)
        if thread is not None and thread["capture_mode"] == "text_only":
            raise ValueError("text-only archive has no local images; upgrade to full before exporting")
        repo = JobsRepository(conn)
        payload = {key: value for key, value in {"tid": tid, "strategy": strategy}.items() if value is not None}
        job = repo.find_live_job_for_thread(job_type=JobType.EXPORT_THREAD.value, tid=tid)
        created = False
        if job is None or not _can_reuse_existing_job(job.payload, payload):
            job = repo.create(
                JobType.EXPORT_THREAD.value,
                tid=tid,
                payload=payload,
            )
            created = True
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={"job_id": job.job_id, "status": "queued", "tid": tid, "created": created},
        resources={"job_events": job_events_uri(job.job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job.job_id}, reason="Poll the export job until it finishes."),
        ],
        side_effects=[
            "job_created" if created else "job_reused",
            "daemon_required",
        ],
    )


def create_thread_update_job(*, tid: int, base_url: str | None = None, connection=None) -> AgentResult:
    payload = create_update_thread_job(tid=tid, base_url=base_url, connection=connection)
    job_id = str(payload["job_id"])
    created = bool(payload.get("created", True))
    return AgentResult(
        ok=True,
        data={"job_id": job_id, "status": "queued", "tid": tid, "created": created},
        resources={"job_events": job_events_uri(job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job_id}, reason="Poll the update job until it finishes."),
        ],
        side_effects=[
            "job_created" if created else "job_reused",
            "daemon_required",
        ],
    )


def sync_forum_range(
    *,
    start_page: int,
    end_page: int,
    forum_id: int = 30,
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> dict[str, object]:
    if start_page <= 0 or end_page <= 0 or end_page < start_page:
        raise ValueError("invalid forum page range")

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        ensure_remote_access_allowed(conn)
        binding = select_random_proxy(settings)
        proxy_url = binding.proxy_url if binding else None
        client = YamiboClient(
            timeout=getattr(settings, "request_timeout_seconds", 15.0),
            cookie_file=cookie_file or str(settings.cookie_file),
            use_system_proxy=settings.use_system_proxy,
            proxy_url=proxy_url,
            login_username=settings.login_username,
            login_password=settings.login_password,
            request_interval=settings.request_interval_seconds,
            request_interval_jitter=settings.request_interval_jitter_seconds,
        )
        collected = []
        scanned_pages: list[str] = []
        seen_tids: set[int] = set()

        try:
            for page in range(start_page, end_page + 1):
                result, items = client.fetch_forum_threads(page=page, base_url=base_url, forum_id=forum_id)
                scanned_pages.append(result.final_url)
                for item in items:
                    if not include_sticky and item.is_sticky:
                        continue
                    if not include_announcements and (item.category or "").strip() == "公告":
                        continue
                    if item.tid in seen_tids:
                        continue
                    seen_tids.add(item.tid)
                    collected.append(item)
        except Exception as exc:
            if is_http_444_error(exc):
                from yamibo_mcp.yamibo.anti_bot import handle_http_444
                handle_http_444(
                    conn,
                    source="archive_commands:sync_forum_range",
                    exc=exc,
                    context={"start_page": start_page, "end_page": end_page, "forum_id": forum_id},
                )
            raise

        repo = JobsRepository(conn)
        job_items: list[dict[str, object]] = []
        for item in collected:
            thread_url = thread_url_from_tid(item.tid, base_url=base_url)
            job = repo.create(
                JobType.SYNC_THREAD.value,
                tid=item.tid,
                payload={"tid": item.tid, "url": thread_url, "base_url": base_url, "forum_id": forum_id},
            )
            job_items.append(
                {
                    "job_id": job.job_id,
                    "tid": item.tid,
                    "title": item.title,
                    "url": thread_url,
                    "category": item.category,
                }
            )
        return {
            "start_page": start_page,
            "end_page": end_page,
            "include_sticky": include_sticky,
            "include_announcements": include_announcements,
            "scanned_pages": scanned_pages,
            "count": len(job_items),
            "items": job_items,
        }
    finally:
        conn.close()
