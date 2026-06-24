from __future__ import annotations

from typing import Any

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.application.update_commands import create_update_thread_job
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.resource_uris import job_events_uri, thread_summary_uri
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.urls import thread_url_from_tid


def _can_reuse_existing_job(existing_payload: dict[str, Any], requested_payload: dict[str, Any]) -> bool:
    return existing_payload == requested_payload


def archive_thread_job(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, Any]:
    if not html_path and not tid and not url:
        raise ValueError("archive_thread requires html_path or tid or url")
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        payload = {
            key: value
            for key, value in {
                "html_path": html_path,
                "tid": tid,
                "url": url,
                "base_url": base_url,
                "forum_id": forum_id,
            }.items()
            if value is not None
        }
        if tid is not None:
            existing = repo.find_live_job_for_thread(job_type=JobType.SYNC_THREAD.value, tid=tid)
            if existing is not None and _can_reuse_existing_job(existing.payload, payload):
                return {"job_id": existing.job_id, "created": False}
        job = repo.create(JobType.SYNC_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id, "created": True}
    finally:
        conn.close()


def create_thread_archive_job(
    *,
    tid: int | None = None,
    url: str | None = None,
    html_path: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    payload = archive_thread_job(
        tid=tid,
        url=url,
        html_path=html_path,
        base_url=base_url,
        forum_id=forum_id,
    )
    job_id = str(payload["job_id"])
    created = bool(payload.get("created", True))
    return AgentResult(
        ok=True,
        data={"job_id": job_id, "status": "queued", "created": created},
        resources={"job_events": job_events_uri(job_id)},
        next_actions=[
            AgentAction(tool="read_job", args={"job_id": job_id}, reason="Poll the queued archive job."),
        ],
        side_effects=[
            "sqlite_job_created" if created else "sqlite_job_reused",
            "daemon_required",
        ],
    )


def create_thread_archive_batch_jobs(
    *,
    tids: list[int],
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    normalized_tids = list(dict.fromkeys(int(tid) for tid in tids if tid))
    if not normalized_tids:
        raise ValueError("tids required")
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = JobsRepository(conn)
        created_job_ids: list[str] = []
        reused_job_ids: list[str] = []
        for tid in normalized_tids:
            payload = {
                key: value
                for key, value in {
                    "tid": tid,
                    "base_url": base_url,
                    "forum_id": forum_id,
                }.items()
                if value is not None
            }
            existing = repo.find_live_job_for_thread(job_type=JobType.SYNC_THREAD.value, tid=tid)
            if existing is not None and _can_reuse_existing_job(existing.payload, payload):
                reused_job_ids.append(existing.job_id)
                continue
            job = repo.create(JobType.SYNC_THREAD.value, tid=tid, payload=payload)
            created_job_ids.append(job.job_id)
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "job_type": JobType.SYNC_THREAD.value,
            "target_count": len(normalized_tids),
            "created_count": len(created_job_ids),
            "reused_count": len(reused_job_ids),
            "created_job_ids": created_job_ids,
            "reused_job_ids": reused_job_ids,
            "tids": normalized_tids,
        },
        side_effects=["sqlite_job_created" if created_job_ids else "sqlite_job_reused", "daemon_required"],
    )


def ensure_thread_archived(
    *,
    tid: int,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        thread = ThreadsRepository(conn).get_thread(tid)
    finally:
        conn.close()

    if thread is not None:
        return AgentResult(
            ok=True,
            data={
                "tid": tid,
                "archived": True,
                "archive_status": thread["archive_status"],
                "source": "local_archive",
            },
            resources={"summary": thread_summary_uri(tid)},
        )

    result = create_thread_archive_job(tid=tid, base_url=base_url, forum_id=forum_id)
    if result.data is not None:
        result.data["tid"] = tid
        result.data["archived"] = False
        result.data["source"] = "job_created"
    return result


def create_thread_export_job(*, tid: int, strategy: str | None = None) -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
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
            "sqlite_job_created" if created else "sqlite_job_reused",
            "daemon_required",
        ],
    )


def create_thread_update_job(*, tid: int, base_url: str | None = None) -> AgentResult:
    payload = create_update_thread_job(tid=tid, base_url=base_url)
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
            "sqlite_job_created" if created else "sqlite_job_reused",
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
    client = YamiboClient(
        timeout=getattr(settings, "request_timeout_seconds", 15.0),
        cookie_file=cookie_file or str(settings.cookie_file),
        use_system_proxy=settings.use_system_proxy,
        login_username=settings.login_username,
        login_password=settings.login_password,
        request_interval=settings.request_interval_seconds,
        request_interval_jitter=settings.request_interval_jitter_seconds,
    )
    collected = []
    scanned_pages: list[str] = []
    seen_tids: set[int] = set()

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

    conn = connect(settings.db_path)
    try:
        migrate(conn)
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
