from __future__ import annotations

from typing import Any

from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.application.thread_context import build_obsidian_context_payload
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.markdown import render_archive_markdown, render_obsidian_markdown
from yamibo_mcp.storage.paths import StoragePaths


def preview_thread_context(*, tid: int, context_format_version: str = "obsidian-md-v2") -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        repo = ThreadsRepository(conn)
        snapshot = _load_thread_snapshot(repo, tid)
        if snapshot is None:
            raise ValueError(f"thread not found: {tid}")
        thread_row = repo.get_thread(tid)
        forum_id = _row_get(thread_row, "forum_id")
        metadata, cleaner_output = build_obsidian_context_payload(
            snapshot,
            forum_id=forum_id,
            archive_status=_row_get(thread_row, "archive_status") or "complete",
            reply_count=_row_get(thread_row, "remote_reply_count")
            or _row_get(thread_row, "local_reply_count"),
            sync_time=_row_get(thread_row, "sync_time") or "",
            context_source="db_preview",
        )
        paths = StoragePaths(settings.data_dir)
        if context_format_version == "obsidian-md-v2":
            markdown = render_obsidian_markdown(snapshot, metadata=metadata, cleaner_output=cleaner_output)
        else:
            markdown = render_archive_markdown(snapshot)
        return AgentResult(ok=True, data={"tid": tid, "context_format_version": context_format_version, "markdown": markdown, "path": str(paths.thread_context(tid))})
    finally:
        conn.close()


def _load_thread_snapshot(repo: ThreadsRepository, tid: int) -> ThreadSnapshot | None:
    thread = repo.get_thread(tid)
    if thread is None:
        return None
    title_row = repo.get_title_parse(tid)
    floors = repo.list_floors(tid)
    title = _build_title_snapshot(thread, title_row)
    floor_snapshots = [
        FloorSnapshot(
            pid=int(row["pid"]),
            tid=int(row["tid"]),
            floor_no=int(row["floor_no"]),
            publisher=row["publisher"],
            content=row["content"] or "",
            pub_time=row["pub_time"],
            has_images=bool(row["has_images"]),
            publisher_uid=row["publisher_uid"],
            image_urls=list(_parse_json_array(row["image_urls_json"])) if "image_urls_json" in row.keys() else [],
            quote_text=row["quote_text"] if "quote_text" in row.keys() else None,
            reply_text=row["reply_text"] if "reply_text" in row.keys() else None,
            rich_body_html=row["rich_body_html"] if "rich_body_html" in row.keys() else None,
        )
        for row in floors
    ]
    return ThreadSnapshot(
        tid=int(thread["tid"]),
        url=None,
        page_type=thread["page_type"],
        raw_title=thread["raw_title"],
        display_title=thread["display_title"],
        title=title,
        publisher=thread["publisher"],
        publisher_uid=thread["publisher_uid"],
        pub_time=thread["pub_time"],
        permission=int(thread["permission"] or 0),
        floors=floor_snapshots,
        image_count=int(thread["image_count"] or 0),
    )


def _build_title_snapshot(thread: Any, title_row: Any) -> TitleSnapshot:
    if title_row is None:
        return TitleSnapshot(
            raw_title=thread["raw_title"],
            display_title=thread["display_title"],
            group_name=None,
            author_guess=thread["publisher"],
            core_title_guess=thread["display_title"],
            normalized_core_title=thread["display_title"],
            series_key=thread["display_title"],
            title_aliases=[],
            chapter_name=None,
            chapter_index=None,
            chapter_index_end=None,
            chapter_title=None,
            subtitle=None,
            tags=[],
            confidence=1.0,
            needs_review=False,
            parser_version="db-preview",
        )
    return TitleSnapshot(
        raw_title=title_row["raw_title"],
        display_title=title_row["display_title"],
        group_name=title_row["group_name"],
        author_guess=title_row["author_guess"],
        core_title_guess=title_row["core_title_guess"],
        normalized_core_title=title_row["normalized_core_title"],
        series_key=title_row["series_key"],
        title_aliases=_parse_json_array(title_row["title_aliases_json"]),
        chapter_name=title_row["chapter_name"],
        chapter_index=title_row["chapter_index"],
        chapter_index_end=title_row["chapter_index_end"],
        chapter_title=title_row["chapter_title"],
        subtitle=title_row["subtitle"],
        tags=_parse_json_array(title_row["tags_json"]),
        confidence=float(title_row["confidence"] or 1.0),
        needs_review=bool(title_row["needs_review"]),
        parser_version=title_row["parser_version"] or "db-preview",
    )


def _parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    import json

    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if hasattr(row, "get"):
        return row.get(key)
    return row[key]
