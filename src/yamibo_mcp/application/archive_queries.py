from __future__ import annotations

import json
from pathlib import Path

from yamibo_mcp.application.contracts import AgentAction, AgentError, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.server.resource_uris import (
    thread_assets_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_export_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_summary_uri,
)
from yamibo_mcp.server.schemas import thread_summary_payload
from yamibo_mcp.storage.paths import StoragePaths

ARCHIVED_THREAD_VIEWS = {"summary", "content", "assets", "diagnostics", "export", "metadata"}
DEFAULT_CONTENT_CHUNK_SIZE = 20
MAX_CONTENT_CHUNK_SIZE = 50


def read_archived_thread(
    *,
    tid: int,
    view: str,
    floor_start: int | None = None,
    floor_end: int | None = None,
    cursor: str | None = None,
    chunk_size: int | None = None,
) -> AgentResult:
    if view not in ARCHIVED_THREAD_VIEWS:
        raise ValueError(f"unsupported view: {view}")
    if floor_start is not None and floor_start <= 0:
        raise ValueError("floor_start must be positive")
    if floor_end is not None and floor_end <= 0:
        raise ValueError("floor_end must be positive")
    if floor_start is not None and floor_end is not None and floor_end < floor_start:
        raise ValueError("floor_end must be greater than or equal to floor_start")

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        thread = repo.get_thread(tid)
        if thread is None:
            return AgentResult(
                ok=False,
                error=AgentError(
                    code="LOCAL_ARCHIVE_NOT_FOUND",
                    message=f"Thread {tid} is not archived locally.",
                    agent_hint="Call create_thread_archive_job or ensure_thread_archived before reading local archive views.",
                    suggested_actions=[
                        AgentAction(
                            tool="create_thread_archive_job",
                            args={"tid": tid},
                            reason="Create a local archive before reading this thread.",
                        )
                    ],
                ),
            )

        title = repo.get_title_parse(tid)
        floor_count = repo.count_floors(tid)
        paths = StoragePaths(
            settings.data_dir,
            export_dir=settings.export_dir,
            novel_txt_export_dir=settings.novel_txt_export_dir,
        )
        resources = {
            "summary": thread_summary_uri(tid),
            "posts": thread_posts_uri(tid),
            "assets": thread_assets_uri(tid),
            "diagnostics": thread_diagnostics_uri(tid),
            "metadata": thread_metadata_uri(tid),
            "context": thread_context_uri(tid),
        }
        if thread["export_path"]:
            resources["export"] = thread_export_uri(tid)

        data: dict[str, object]
        if view == "summary":
            data = _build_summary(thread, title, floor_count=floor_count)
        elif view == "content":
            offset = _parse_content_cursor(cursor)
            limit = _normalize_chunk_size(chunk_size)
            total_in_range = repo.count_floors_window(tid, floor_start=floor_start, floor_end=floor_end)
            selected_floors = repo.list_floors_window(
                tid,
                floor_start=floor_start,
                floor_end=floor_end,
                limit=limit,
                offset=offset,
            )
            next_offset = offset + len(selected_floors)
            has_more = next_offset < total_in_range
            next_cursor = f"offset:{next_offset}" if has_more else None
            selected_pids = [int(floor["pid"]) for floor in selected_floors]
            blocks = ContentBlocksRepository(conn).list_blocks_for_pids(tid, selected_pids)
            data = {
                **_build_summary(thread, title, floor_count=floor_count),
                "view": "content",
                "floor_range": {"start": floor_start, "end": floor_end, "total": total_in_range},
                "cursor": cursor,
                "chunk_size": limit,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "floors": [
                    {
                        "pid": floor["pid"],
                        "floor_no": floor["floor_no"],
                        "publisher": floor["publisher"],
                        "pub_time": floor["pub_time"],
                        "has_images": bool(floor["has_images"]),
                        "content": floor["content"] or "",
                    }
                    for floor in selected_floors
                ],
                "content_blocks": [
                    {
                        "pid": block["pid"],
                        "order_index": block["order_index"],
                        "block_type": block["block_type"],
                        "text": block["text"],
                        "asset_id": block["asset_id"],
                    }
                    for block in blocks
                ],
                "resource_hints": _content_resource_hints(
                    tid=tid,
                    next_cursor=next_cursor,
                    chunk_size=limit,
                    floor_start=floor_start,
                    floor_end=floor_end,
                    resources=resources,
                ),
            }
        elif view == "assets":
            assets = AssetsRepository(conn).list_assets(tid)
            data = {
                **_build_summary(thread, title, floor_count=floor_count),
                "view": "assets",
                "assets": [
                    {
                        "asset_id": asset["asset_id"],
                        "pid": asset["pid"],
                        "asset_type": asset["asset_type"],
                        "remote_url": asset["remote_url"],
                        "local_path": asset["local_path"],
                        "required": bool(asset["required"]),
                        "exportable": bool(asset["exportable"]),
                        "status": asset["status"],
                    }
                    for asset in assets
                ],
            }
        elif view == "diagnostics":
            missing_images = json.loads(thread["missing_images_json"] or "[]")
            validation_errors = json.loads(thread["validation_errors_json"] or "[]") if thread["validation_errors_json"] else []
            data = {
                **_build_summary(thread, title, floor_count=floor_count),
                "view": "diagnostics",
                "missing_images": missing_images,
                "validation_errors": validation_errors,
                "needs_title_review": bool(thread["needs_title_review"]),
                "needs_series_review": bool(thread["needs_series_review"]),
            }
        elif view == "metadata":
            metadata_path = paths.thread_metadata(tid)
            data = {
                **_build_summary(thread, title, floor_count=floor_count),
                "view": "metadata",
                "metadata_exists": metadata_path.exists(),
                "metadata": _read_json_file(metadata_path),
            }
        else:
            export_path = _resolve_export_path(settings.data_dir, paths, tid, thread["export_path"])
            data = {
                **_build_summary(thread, title, floor_count=floor_count),
                "view": "export",
                "export_exists": export_path.exists(),
                "export_path": str(export_path),
            }

        return AgentResult(ok=True, data=data, resources=resources)
    finally:
        conn.close()


def probe_archived_threads(*, tids: list[int]) -> AgentResult:
    normalized_tids: list[int] = []
    seen: set[int] = set()
    for raw_tid in tids:
        tid = int(raw_tid)
        if tid <= 0:
            raise ValueError("tids must contain positive integers")
        if tid in seen:
            continue
        seen.add(tid)
        normalized_tids.append(tid)
    if not normalized_tids:
        raise ValueError("tids required")

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        items = ThreadsRepository(conn).probe_archive_states(normalized_tids)
        return AgentResult(
            ok=True,
            data={
                "count": len(items),
                "items": items,
            },
        )
    finally:
        conn.close()


def list_exports(*, limit: int = 100) -> dict[str, object]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        rows = ThreadsRepository(conn).list_exports(limit=limit)
        return {"count": len(rows), "items": [thread_summary_payload(row, include_export=True) for row in rows]}
    finally:
        conn.close()


def read_forum_profiles() -> AgentResult:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        rows = conn.execute(
            "SELECT forum_id, name, name_en, content_kind, base_url, enabled FROM forums ORDER BY forum_id"
        ).fetchall()
    finally:
        conn.close()

    return AgentResult(
        ok=True,
        data={
            "forums": [
                {
                    "forum_id": row["forum_id"],
                    "name": row["name"],
                    "name_en": row["name_en"],
                    "content_kind": row["content_kind"],
                    "base_url": row["base_url"],
                    "enabled": bool(row["enabled"]),
                }
                for row in rows
            ]
        },
    )


def _build_summary(thread, title, *, floor_count: int) -> dict[str, object]:
    row = thread
    if title is not None:
        row = dict(thread)
        row["core_title_guess"] = title["core_title_guess"]
        row["chapter_name"] = title["chapter_name"]
        row["chapter_title"] = title["chapter_title"]
        row["series_key"] = title["series_key"]
    summary = thread_summary_payload(row, include_export=True)
    summary["floor_count"] = floor_count
    summary["title_parse"] = None if title is None else {
        "group_name": title["group_name"],
        "author_guess": title["author_guess"],
        "core_title_guess": title["core_title_guess"],
        "chapter_name": title["chapter_name"],
        "chapter_title": title["chapter_title"],
        "series_key": title["series_key"],
        "confidence": title["confidence"],
        "needs_review": bool(title["needs_review"]),
    }
    return summary


def _parse_content_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    raw_offset = cursor.removeprefix("offset:")
    try:
        offset = int(raw_offset)
    except ValueError as exc:
        raise ValueError(f"invalid content cursor: {cursor}") from exc
    if offset < 0:
        raise ValueError("content cursor offset must be non-negative")
    return offset


def _normalize_chunk_size(chunk_size: int | None) -> int:
    if chunk_size is None:
        return DEFAULT_CONTENT_CHUNK_SIZE
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return min(chunk_size, MAX_CONTENT_CHUNK_SIZE)


def _content_resource_hints(
    *,
    tid: int,
    next_cursor: str | None,
    chunk_size: int,
    floor_start: int | None,
    floor_end: int | None,
    resources: dict[str, str],
) -> dict[str, object]:
    hints: dict[str, object] = {
        "summary_resource": resources["summary"],
        "posts_resource": resources["posts"],
        "context_resource": resources["context"],
    }
    if next_cursor is not None:
        hints["next_page_tool_call"] = {
            "tool": "read_archived_thread",
            "args": {
                "tid": tid,
                "view": "content",
                "cursor": next_cursor,
                "chunk_size": chunk_size,
                **({} if floor_start is None else {"floor_start": floor_start}),
                **({} if floor_end is None else {"floor_end": floor_end}),
            },
        }
    return hints


def _read_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_export_path(data_dir: Path, paths: StoragePaths, tid: int, export_value: str | None) -> Path:
    if export_value:
        export_path = Path(export_value)
        return export_path if export_path.is_absolute() else data_dir / export_path
    return paths.thread_export_zip(tid)
