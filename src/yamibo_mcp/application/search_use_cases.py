from __future__ import annotations

from yamibo_mcp.application.contracts import AgentResult


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
    from yamibo_mcp.application.remote_queries import browse_forum_page as remote_browse_forum_page

    payload = remote_browse_forum_page(
        page=page,
        forum_id=forum_id,
        order=order,
        base_url=base_url,
        cookie_file=cookie_file,
        include_sticky=include_sticky,
        include_announcements=include_announcements,
    )
    return AgentResult(ok=True, data=payload, side_effects=["remote_fetch_only"])


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
    from yamibo_mcp.application.remote_queries import search_threads as remote_search_threads

    payload = remote_search_threads(
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
    if "limit" in payload:
        payload.pop("limit", None)
    return AgentResult(ok=True, data=payload, side_effects=["remote_fetch_only"])
