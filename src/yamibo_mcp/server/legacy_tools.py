from __future__ import annotations

import json
from pathlib import Path

from yamibo_mcp.application.remote_queries import browse_forum_page, search_threads
from yamibo_mcp.application.update_commands import create_update_thread_job as _create_update_thread_job
from yamibo_mcp.application.update_queries import check_thread_updates
from yamibo_mcp.application.legacy_use_cases import archive_thread_job, ensure_thread, get_thread as legacy_get_thread
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.server.resources import (
    generate_series_chapters_json,
    generate_series_index_markdown,
    read_resource,
    read_resource_content,
)
from yamibo_mcp.server.schemas import thread_summary_payload
from yamibo_mcp.services.llm_client import openai_compatible_chat
from yamibo_mcp.services.title_llm import refine_title_parse_with_llm
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.title.parser import parse_title
from yamibo_mcp.yamibo.urls import thread_url_from_tid


def create_noop_job() -> str:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        job = JobsRepository(conn).create(JobType.NOOP.value)
        return job.job_id
    finally:
        conn.close()


def archive_thread(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, object]:
    return archive_thread_job(html_path=html_path, tid=tid, url=url, base_url=base_url, forum_id=forum_id)


def sync_thread(
    *,
    html_path: str | None = None,
    tid: int | None = None,
    url: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> dict[str, object]:
    return archive_thread(html_path=html_path, tid=tid, url=url, base_url=base_url, forum_id=forum_id)


def export_thread(*, tid: int, strategy: str | None = None) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        payload = {"tid": tid}
        if strategy:
            payload["strategy"] = strategy
        job = JobsRepository(conn).create(JobType.EXPORT_THREAD.value, tid=tid, payload=payload)
        return {"job_id": job.job_id}
    finally:
        conn.close()


def update_thread(*, tid: int, base_url: str | None = None) -> dict[str, object]:
    return _create_update_thread_job(tid=tid, base_url=base_url)


def cleanup_job(*, job_id: str | None = None, mode: str = "job_staging", older_than_hours: int | None = None) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        payload = {"mode": mode}
        if job_id is not None:
            payload["job_id"] = job_id
        if older_than_hours is not None:
            payload["older_than_hours"] = older_than_hours
        job = JobsRepository(conn).create(JobType.CLEANUP_JOB.value, payload=payload)
        return {"job_id": job.job_id}
    finally:
        conn.close()


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


def get_job_status(job_id: str) -> dict[str, object]:
    from yamibo_mcp.application.job_queries import get_job_status_payload

    return get_job_status_payload(job_id)


def get_thread(*, tid: int, url: str | None = None, base_url: str | None = None, forum_id: int | None = None) -> dict[str, object]:
    return legacy_get_thread(tid=tid, url=url, base_url=base_url, forum_id=forum_id)


def list_exports(*, limit: int = 100) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        rows = ThreadsRepository(conn).list_exports(limit=limit)
        return {"count": len(rows), "items": [thread_summary_payload(row, include_export=True) for row in rows]}
    finally:
        conn.close()


def parse_thread_title(*, title: str, use_llm_on_low_confidence: bool = True) -> dict[str, object]:
    settings = load_settings()
    parsed = parse_title(title)
    llm_used = False
    llm_meta = None
    if use_llm_on_low_confidence:
        parsed, llm_meta = refine_title_parse_with_llm(settings, raw_title=title, parsed=parsed)
        llm_used = False if llm_meta is None else bool(llm_meta.get("used"))
    return {
        "display_title": parsed.display_title,
        "group_name": parsed.group_name,
        "author_guess": parsed.author_guess,
        "core_title_guess": parsed.core_title_guess,
        "normalized_core_title": parsed.normalized_core_title,
        "series_key": parsed.series_key,
        "title_aliases": parsed.title_aliases,
        "chapter_name": parsed.chapter_name,
        "chapter_index": parsed.chapter_index,
        "chapter_index_end": parsed.chapter_index_end,
        "chapter_title": parsed.chapter_title,
        "subtitle": parsed.subtitle,
        "tags": parsed.tags,
        "confidence": parsed.confidence,
        "needs_review": parsed.needs_review,
        "llm_used": llm_used,
        "llm_meta": llm_meta if use_llm_on_low_confidence else None,
    }


def llm_transform_text(
    *,
    task: str,
    text: str,
    system_prompt: str | None = None,
    temperature: float = 0.0,
) -> dict[str, object]:
    settings = load_settings()
    prompt = system_prompt or "You extract or clean forum text. Return concise structured plain text unless JSON is explicitly requested."
    result = openai_compatible_chat(
        settings,
        system_prompt=prompt,
        user_prompt=f"Task: {task}\n\nInput:\n{text}",
        temperature=temperature,
    )
    return {"task": task, "model": result["model"], "content": result["content"]}


def dump_json(data: dict[str, object]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def create_sync_thread_job(*, html_path: str, tid: int | None = None, url: str | None = None) -> str:
    return str(archive_thread(html_path=html_path, tid=tid, url=url)["job_id"])


def create_export_thread_job(*, tid: int) -> str:
    return str(export_thread(tid=tid)["job_id"])


def create_update_thread_job(*, tid: int, base_url: str | None = None) -> str:
    return str(update_thread(tid=tid, base_url=base_url)["job_id"])
