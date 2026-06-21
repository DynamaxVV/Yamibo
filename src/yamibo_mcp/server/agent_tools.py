from __future__ import annotations

from yamibo_mcp.application.archive_commands import (
    create_thread_archive_job as _create_thread_archive_job,
    create_thread_export_job as _create_thread_export_job,
    create_thread_update_job as _create_thread_update_job,
    ensure_thread_archived as _ensure_thread_archived,
)
from yamibo_mcp.application.archive_queries import (
    read_archived_thread as _read_archived_thread,
)
from yamibo_mcp.application.forum_queries import read_forum_profiles as _read_forum_profiles
from yamibo_mcp.application.job_queries import read_job as _read_job
from yamibo_mcp.application.job_queries import read_job_events as _read_job_events
from yamibo_mcp.application.remote_queries import inspect_remote_thread as _inspect_remote_thread
from yamibo_mcp.application.search_use_cases import browse_forum_page as _browse_forum_page
from yamibo_mcp.application.search_use_cases import search_forum_threads as _search_forum_threads
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
    return _browse_forum_page(
        page=page,
        forum_id=forum_id,
        order=order,
        base_url=base_url,
        cookie_file=cookie_file,
        include_sticky=include_sticky,
        include_announcements=include_announcements,
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
    return _search_forum_threads(
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


@agent_tool
def inspect_remote_thread(
    *,
    tid: int,
    forum_id: int | None = None,
    author_only: bool = False,
    base_url: str | None = None,
) -> AgentResult:
    return _inspect_remote_thread(
        tid=tid,
        forum_id=forum_id,
        author_only=author_only,
        base_url=base_url,
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
def check_thread_updates(*, tid: int, base_url: str | None = None) -> AgentResult:
    return AgentResult(ok=True, data=_check_thread_updates(tid=tid, base_url=base_url), side_effects=["remote_fetch_only"])


@agent_tool
def create_thread_update_job(*, tid: int, base_url: str | None = None) -> AgentResult:
    return _create_thread_update_job(tid=tid, base_url=base_url)


@agent_tool
def create_thread_export_job(*, tid: int, strategy: str | None = None) -> AgentResult:
    return _create_thread_export_job(tid=tid, strategy=strategy)


@agent_tool
def read_job(*, job_id: str) -> AgentResult:
    return _read_job(job_id=job_id)


@agent_tool
def read_job_events(*, job_id: str) -> AgentResult:
    return _read_job_events(job_id=job_id)


@agent_tool
def read_forum_profiles() -> AgentResult:
    return _read_forum_profiles()
