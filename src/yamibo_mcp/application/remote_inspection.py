from __future__ import annotations

from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.thread_detail import (
    extract_category_from_html,
    extract_forum_id_from_html,
    parse_thread_snapshot,
)


def inspect_remote_thread(
    *,
    tid: int,
    forum_id: int | None = None,
    author_only: bool = False,
    base_url: str | None = None,
) -> AgentResult:
    settings = load_settings()
    client = YamiboClient(
        cookie_file=str(settings.cookie_file),
        use_system_proxy=settings.use_system_proxy,
        login_username=settings.login_username,
        login_password=settings.login_password,
        request_interval=settings.request_interval_seconds,
        request_interval_jitter=settings.request_interval_jitter_seconds,
    )
    fetched = client.fetch_thread(tid=tid, base_url=base_url)
    snapshot = parse_thread_snapshot(fetched.html, url=fetched.final_url, tid=tid)
    resolved_forum_id = forum_id if forum_id is not None else extract_forum_id_from_html(fetched.html)
    category = extract_category_from_html(fetched.html)
    floor_preview = []
    for floor in snapshot.floors[:3]:
        content = (floor.content or "").strip()
        floor_preview.append(
            {
                "floor_no": floor.floor_no,
                "publisher": floor.publisher,
                "content_preview": content[:200],
            }
        )

    return AgentResult(
        ok=True,
        data={
            "tid": snapshot.tid,
            "title": snapshot.display_title,
            "publisher": snapshot.publisher,
            "publisher_uid": snapshot.publisher_uid,
            "forum_id": resolved_forum_id,
            "category": category,
            "floor_count": len(snapshot.floors),
            "image_url_count": snapshot.image_count,
            "author_only": author_only,
            "preview": floor_preview,
            "remote_url": fetched.final_url,
        },
        side_effects=["remote_fetch_only"],
    )
