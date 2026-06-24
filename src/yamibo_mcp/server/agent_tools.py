from __future__ import annotations

from yamibo_mcp.application.archive_commands import (
    create_thread_archive_batch_jobs as _create_thread_archive_batch_jobs,
    create_thread_archive_job as _create_thread_archive_job,
    create_thread_export_job as _create_thread_export_job,
    create_thread_update_job as _create_thread_update_job,
    ensure_thread_archived as _ensure_thread_archived,
)
from yamibo_mcp.application.archive_queries import (
    read_archived_thread as _read_archived_thread,
    probe_archived_threads as _probe_archived_threads,
    read_forum_profiles as _read_forum_profiles,
)
from yamibo_mcp.application.job_queries import read_job as _read_job
from yamibo_mcp.application.job_queries import read_job_events as _read_job_events
from yamibo_mcp.application.job_queries import wait_for_job as _wait_for_job
from yamibo_mcp.application.rag_commands import (
    create_rag_index_batch_jobs as _create_rag_index_batch_jobs,
    create_rag_index_job as _create_rag_index_job,
)
from yamibo_mcp.application.rag_queries import search_archived_content as _search_archived_content
from yamibo_mcp.application.remote_queries import (
    browse_forum_page as _browse_forum_page,
    inspect_remote_thread as _inspect_remote_thread,
    search_threads as _search_forum_threads,
)
from yamibo_mcp.application.update_queries import check_thread_updates as _check_thread_updates
from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.server.agent_adapter import agent_tool


@agent_tool
def browse_forum_page(
    *,
    page: int,
    forum_id: int = 30,
    order: str = "default",
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> AgentResult:
    return AgentResult(
        ok=True,
        data=_browse_forum_page(
            page=page,
            forum_id=forum_id,
            order=order,
            base_url=base_url,
            cookie_file=cookie_file,
            include_sticky=include_sticky,
            include_announcements=include_announcements,
        ),
        side_effects=["remote_fetch_only"],
    )


@agent_tool
def search_forum_threads(
    *,
    query: str = "",
    forum_id: int = 30,
    start_page: int = 1,
    end_page: int | None = None,
    posted_on: str | None = None,
    base_url: str = "https://bbs.yamibo.com",
    cookie_file: str | None = None,
    include_sticky: bool = False,
    include_announcements: bool = False,
) -> AgentResult:
    payload = _search_forum_threads(
        query=query,
        forum_id=forum_id,
        start_page=start_page,
        end_page=end_page,
        posted_on=posted_on,
        base_url=base_url,
        cookie_file=cookie_file,
        include_sticky=include_sticky,
        include_announcements=include_announcements,
    )
    payload.pop("limit", None)
    return AgentResult(ok=True, data=payload, side_effects=["remote_fetch_only"])


@agent_tool
def inspect_remote_thread(
    *,
    tid: int,
    forum_id: int | None = None,
    base_url: str | None = None,
) -> AgentResult:
    return AgentResult(
        ok=True,
        data=_inspect_remote_thread(
            tid=tid,
            forum_id=forum_id,
            base_url=base_url,
        ),
        side_effects=["remote_fetch_only"],
    )


@agent_tool
def create_thread_archive_job(
    *,
    tid: int | None = None,
    url: str | None = None,
    html_path: str | None = None,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    return _create_thread_archive_job(
        tid=tid,
        url=url,
        html_path=html_path,
        base_url=base_url,
        forum_id=forum_id,
    )


@agent_tool
def create_thread_archive_batch_jobs(
    *,
    tids: list[int],
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    return _create_thread_archive_batch_jobs(tids=tids, base_url=base_url, forum_id=forum_id)


@agent_tool
def ensure_thread_archived(
    *,
    tid: int,
    base_url: str | None = None,
    forum_id: int | None = None,
) -> AgentResult:
    return _ensure_thread_archived(tid=tid, base_url=base_url, forum_id=forum_id)


@agent_tool
def read_archived_thread(
    *,
    tid: int,
    view: str,
    floor_start: int | None = None,
    floor_end: int | None = None,
    cursor: str | None = None,
    chunk_size: int | None = None,
) -> AgentResult:
    return _read_archived_thread(
        tid=tid,
        view=view,
        floor_start=floor_start,
        floor_end=floor_end,
        cursor=cursor,
        chunk_size=chunk_size,
    )


@agent_tool
def probe_archived_threads(*, tids: list[int]) -> AgentResult:
    return _probe_archived_threads(tids=tids)


@agent_tool
def check_thread_updates(*, tid: int, base_url: str | None = None) -> AgentResult:
    return AgentResult(ok=True, data=_check_thread_updates(tid=tid, base_url=base_url), side_effects=["remote_fetch_only"])


@agent_tool
def create_thread_update_job(*, tid: int, base_url: str | None = None) -> AgentResult:
    return _create_thread_update_job(tid=tid, base_url=base_url)


@agent_tool
def create_thread_export_job(*, tid: int, strategy: str | None = None) -> AgentResult:
    return _create_thread_export_job(tid=tid, strategy=strategy)


@agent_tool
def create_rag_index_job(
    *,
    tid: int | None = None,
    force: bool = False,
    embedding_dimensions: int | None = None,
) -> AgentResult:
    return _create_rag_index_job(tid=tid, force=force, embedding_dimensions=embedding_dimensions)


@agent_tool
def create_rag_index_batch_jobs(
    *,
    tids: list[int],
    force: bool = False,
    embedding_dimensions: int | None = None,
) -> AgentResult:
    return _create_rag_index_batch_jobs(tids=tids, force=force, embedding_dimensions=embedding_dimensions)


@agent_tool
def search_archived_content(
    *,
    query: str,
    mode: str = "hybrid",
    top_k: int = 10,
    forum_id: int | None = None,
    content_kind: str | None = None,
    tid: int | None = None,
    series_id: int | None = None,
    floor_start: int | None = None,
    floor_end: int | None = None,
) -> AgentResult:
    return _search_archived_content(
        query=query,
        mode=mode,
        top_k=top_k,
        forum_id=forum_id,
        content_kind=content_kind,
        tid=tid,
        series_id=series_id,
        floor_start=floor_start,
        floor_end=floor_end,
    )


@agent_tool
def read_job(*, job_id: str) -> AgentResult:
    return _read_job(job_id=job_id)


@agent_tool
def read_job_events(*, job_id: str) -> AgentResult:
    return _read_job_events(job_id=job_id)


@agent_tool
def wait_for_job(
    *,
    job_id: str,
    timeout_seconds: float = 120,
    poll_interval_seconds: float = 2,
    include_events: bool = False,
) -> AgentResult:
    return _wait_for_job(
        job_id=job_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        include_events=include_events,
    )


@agent_tool
def read_forum_profiles() -> AgentResult:
    return _read_forum_profiles()


PUBLIC_AGENT_TOOLS = [
    ("search_forum_threads", "Remote-first, read-only forum search. Uses forum pagination and compact archive hints; does not expose a limit parameter.", search_forum_threads),
    ("browse_forum_page", "Remote read-only forum page browse. Returns one page of compact thread items and never creates jobs.", browse_forum_page),
    ("inspect_remote_thread", "Remote read-only thread preview. Fetches and parses a compact snapshot without writing SQLite, downloading assets, or creating jobs.", inspect_remote_thread),
    ("create_thread_archive_job", "Create a background archive job for a thread or local HTML input. Side effect: writes a queued job to SQLite; daemon execution is required.", create_thread_archive_job),
    ("create_thread_archive_batch_jobs", "Create background archive jobs for multiple thread ids. Side effect: writes queued jobs to SQLite; daemon execution is required.", create_thread_archive_batch_jobs),
    ("ensure_thread_archived", "Local archive check plus job creation fallback. Returns local archive state if present, otherwise creates an archive job.", ensure_thread_archived),
    ("read_archived_thread", "Read local archive views from SQLite and materialized files. Views are local-only and never trigger remote fetches.", read_archived_thread),
    ("probe_archived_threads", "Batch read-only archive probe for multiple tids. Returns local archive state and the last local floor timestamp without creating jobs or fetching remote data.", probe_archived_threads),
    ("check_thread_updates", "Remote read-only update inspection for archived novel threads. Does not create jobs.", check_thread_updates),
    ("create_thread_update_job", "Create a background incremental update job for an archived novel thread. Side effect: writes a queued job to SQLite.", create_thread_update_job),
    ("create_thread_export_job", "Create a background export job for a local archive. Side effect: writes a queued job to SQLite.", create_thread_export_job),
    ("create_rag_index_job", "Create a background RAG indexing job for one archived thread. Side effect: writes a queued job to SQLite.", create_rag_index_job),
    ("create_rag_index_batch_jobs", "Create background RAG indexing jobs for multiple archived threads. Side effect: writes queued jobs to SQLite.", create_rag_index_batch_jobs),
    ("search_archived_content", "Search local archived text content with keyword, vector, or hybrid ranking. Never fetches remote forum data.", search_archived_content),
    ("read_job", "Read compact job status from the local SQLite queue.", read_job),
    ("read_job_events", "Read persisted job event history for a queued or completed job.", read_job_events),
    ("wait_for_job", "Wait for a background job to reach a terminal state without client-side sleep.", wait_for_job),
    ("read_forum_profiles", "Read configured forum profiles and content-type guidance from local metadata.", read_forum_profiles),
]
