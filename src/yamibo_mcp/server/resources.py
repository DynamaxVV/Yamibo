from __future__ import annotations

import json
import inspect
from pathlib import Path

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.server.resource_uris import (
    build_resource_payload,
    agent_workflows_guide_uri,
    agent_evaluation_guide_uri,
    archive_model_guide_uri,
    error_codes_guide_uri,
    guess_content_type,
    job_events_uri,
    parse_resource_uri,
    series_index_uri,
    series_chapters_uri,
    forum_summary_uri,
    forums_index_uri,
    thread_export_uri,
    thread_assets_uri,
    thread_context_uri,
    thread_diagnostics_uri,
    thread_metadata_uri,
    thread_posts_uri,
    thread_summary_uri,
    thread_update_check_uri,
    tools_schema_uri,
)
from yamibo_mcp.storage.paths import StoragePaths


def read_resource(uri: str) -> dict[str, object]:
    kind_root, tid, kind = parse_resource_uri(uri)
    if kind_root == "guide":
        return _build_guide_resource(uri, kind)
    if kind_root == "schema" and kind == "tools":
        return _build_tools_schema_resource(uri)

    settings = load_settings()
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir)
    if kind_root == "threads":
        assert tid is not None
        if kind == "context":
            path = paths.thread_context(tid)
        elif kind == "metadata":
            path = paths.thread_metadata(tid)
        elif kind == "export":
            path = _resolve_thread_export_path(settings, paths, tid)
        elif kind == "summary":
            return _build_thread_summary_resource(uri, tid, settings)
        elif kind == "diagnostics":
            return _build_thread_diagnostics_resource(uri, tid, settings)
        elif kind == "posts":
            return _build_thread_posts_resource(uri, tid, settings)
        elif kind == "assets":
            return _build_thread_assets_resource(uri, tid, settings)
        elif kind == "update-check":
            return _build_thread_update_check_resource(uri, tid)
        else:
            raise ValueError(f"unsupported resource kind: {kind}")
        content_type = guess_content_type(kind)
        if kind == "export":
            content_type = "text/plain" if path.suffix.lower() == ".txt" else "application/zip"
        payload = build_resource_payload(uri=uri, path=path, content_type=content_type)
        if payload["exists"]:
            if kind == "export":
                payload["size_bytes"] = path.stat().st_size
            else:
                payload["text"] = path.read_text(encoding="utf-8")
        return payload
    if kind_root == "series" and kind == "index":
        path = paths.series_index()
        text = generate_series_index_markdown(write_path=path)
        payload = build_resource_payload(uri=uri, path=path, content_type=guess_content_type(kind))
        payload["exists"] = True
        payload["text"] = text
        return payload
    if kind_root == "series" and kind == "chapters":
        assert tid is not None
        path = paths.series_chapters(tid)
        text = generate_series_chapters_json(series_id=tid, write_path=path)
        payload = build_resource_payload(uri=uri, path=path, content_type=guess_content_type(kind))
        payload["exists"] = True
        payload["text"] = text
        return payload
    if kind_root == "forums" and kind == "index":
        return _build_forums_index_resource(uri, settings)
    if kind_root == "forums" and kind == "summary":
        assert tid is not None
        return _build_forum_summary_resource(uri, tid, settings)
    if kind_root == "jobs" and kind.endswith("/events"):
        job_id = kind.split("/")[0]
        return _build_job_events_resource(uri, job_id, settings)
    raise ValueError(f"unsupported resource root: {kind_root}")


def _build_guide_resource(uri: str, kind: str) -> dict[str, object]:
    guide_text = {
        "agent-workflows": _agent_workflows_guide(),
        "error-codes": _error_codes_guide(),
        "archive-model": _archive_model_guide(),
        "agent-evaluation": _agent_evaluation_guide(),
    }.get(kind)
    if guide_text is None:
        raise ValueError(f"unsupported guide resource: {kind}")
    return {"uri": uri, "content_type": "text/markdown", "exists": True, "text": guide_text}


def _build_tools_schema_resource(uri: str) -> dict[str, object]:
    from yamibo_mcp.server.legacy_protocol import TOOLS

    tools = []
    for name, (handler, description) in TOOLS.items():
        signature = inspect.signature(handler)
        parameters = []
        for param_name, param in signature.parameters.items():
            parameters.append(
                {
                    "name": param_name,
                    "required": param.default is inspect.Parameter.empty,
                    "default": None if param.default is inspect.Parameter.empty else param.default,
                    "annotation": None if param.annotation is inspect.Parameter.empty else str(param.annotation),
                }
            )
        tools.append({"name": name, "description": description, "parameters": parameters})
    text = json.dumps({"tools": tools}, ensure_ascii=False, indent=2)
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": text}


def _agent_workflows_guide() -> str:
    return f"""# Yamibo Agent Workflows

Start here when you do not know which tool to call.

## Remote discovery
- Use `read_forum_profiles` to understand available forums.
- Use `browse_forum_page` for page-by-page browsing.
- Use `search_forum_threads` for remote-first search.
- Use `inspect_remote_thread` before archiving when you need a small remote preview.

## Local archive workflow
- Use `ensure_thread_archived` when you need a local copy and can tolerate queued work.
- Use `create_thread_archive_job` for explicit job creation.
- Poll `read_job`, then `read_job_events`.
- Read local content with `read_archived_thread`.
- For large content, call `read_archived_thread` with `view="content"` and follow `next_cursor`.

## Update/export workflow
- Use `check_thread_updates` for read-only update inspection.
- Use `create_thread_update_job` only after update inspection or when the user requests it.
- Use `create_thread_export_job` after a local archive exists.

Useful resources:
- `{tools_schema_uri()}`
- `{error_codes_guide_uri()}`
- `{archive_model_guide_uri()}`
"""


def _error_codes_guide() -> str:
    return """# Yamibo Agent Error Codes

- `INVALID_ARGUMENT`: Tool arguments are invalid. Fix the request before retrying.
- `LOCAL_ARCHIVE_NOT_FOUND`: The thread is not archived locally. Use `create_thread_archive_job` or `ensure_thread_archived`.
- `JOB_NOT_FOUND`: The job id is unknown. Check the id or create a new job.
- `REMOTE_LOGIN_REQUIRED`: Remote access needs a valid cookie/login.
- `REMOTE_MAINTENANCE`: The forum appears to be in maintenance mode. Retry later.
- `UNEXPECTED_REMOTE_PAGE`: The remote page is not the expected forum/thread page.
- `REMOTE_FETCH_FAILED`: Remote fetch failed for network or HTTP reasons.
- `EXPORT_PRECHECK_FAILED`: Export cannot start until archive preconditions are fixed.
- `INTERNAL_ERROR`: Unexpected server error. Prefer a narrower retry or inspect job events.
"""


def _archive_model_guide() -> str:
    return f"""# Yamibo Archive Model

The MCP interface separates remote reads, local archives, and background jobs.

## State model
- `browse_forum_page`, `search_forum_threads`, `inspect_remote_thread`, and `check_thread_updates` are remote read-only tools.
- `create_thread_archive_job`, `create_thread_update_job`, and `create_thread_export_job` create SQLite jobs.
- The daemon consumes queued jobs and materializes local files/resources.

## Local content model
- `read_archived_thread(view="summary")` returns compact metadata and resource URIs.
- `read_archived_thread(view="content")` returns a bounded chunk of floors and content blocks.
- Follow `next_cursor` while `has_more` is true.
- Full materialized text is exposed through `{thread_context_uri('{tid}')}`.
- Structured posts are exposed through `{thread_posts_uri('{tid}')}`.

## Job model
- Job status is read through `read_job`.
- Job event history is read through `read_job_events` or `{job_events_uri('{job_id}')}`.
"""


def _agent_evaluation_guide() -> str:
    return """# Yamibo Agent Evaluation

Use this guide when validating whether an agent client can operate Yamibo end to end.

## Passing goals
- The agent should distinguish remote read-only tools from local archive reads and job-creation tools.
- The agent should avoid creating duplicate live jobs for the same thread and payload.
- The agent should poll `read_job` as the primary status surface and only read `read_job_events` for diagnostics.
- The agent should use `read_archived_thread(view="summary")` or paged `view="content"` before reading full materialized files.

## Required scenarios
1. Search or inspect a remote thread without causing local writes.
2. Create an archive job, poll it through `read_job`, then inspect `read_job_events`.
3. Read a missing local archive, recover through job creation, then read `summary` and paged `content`.
4. Observe `failed`, `partial`, and `interrupted` job states and follow the returned hints instead of blindly retrying.
5. Create an export or update job and verify that payload-compatible live jobs are reused, while different payloads create new jobs.

## Recommended scoring
- `discoverability`: can the agent find the workflow from guides and tool descriptions.
- `state_discipline`: does the agent keep remote, local, and async job states separate.
- `recovery`: does the agent react correctly to `JOB_NOT_FOUND`, `LOCAL_ARCHIVE_NOT_FOUND`, `REMOTE_LOGIN_REQUIRED`, `REMOTE_MAINTENANCE`, `partial`, and `interrupted`.
- `token_efficiency`: does the agent prefer compact views and cursor pagination over full-file reads.

## Evidence to capture
- tool call order
- final job status and event timeline
- whether duplicate jobs were created
- whether content pagination followed `next_cursor`
- whether the run completed without human correction
"""


def read_resource_content(uri: str) -> tuple[str | bytes, str]:
    payload = read_resource(uri)
    content_type = str(payload["content_type"])
    if not payload.get("exists"):
        raise FileNotFoundError(f"resource does not exist: {uri}")
    if content_type in {"application/zip", "application/octet-stream"}:
        path = Path(str(payload["path"]))
        return path.read_bytes(), content_type
    return str(payload.get("text") or ""), content_type


def generate_series_index_markdown(*, limit: int = 100, write_path: Path | None = None) -> str:
    from yamibo_mcp.server.schemas import thread_summary_payload

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = SeriesRepository(conn)
        rows = repo.list_series(limit=limit)
        lines = ["# Series Index", ""]
        for row in rows:
            series_id = int(row["series_id"])
            lines.append(f"## {row['canonical_title'] or '(untitled)'}")
            lines.append(f"- series_id: {series_id}")
            lines.append(f"- series_key: {row['series_key'] or ''}")
            lines.append(f"- author: {row['author_guess'] or ''}")
            lines.append(f"- threads: {row['thread_count']}")
            lines.append(f"- chapters_resource_uri: {series_chapters_uri(series_id)}")
            threads = repo.list_threads_for_series(series_id)
            if threads:
                lines.append("- chapters:")
                for thread in threads[:20]:
                    thread_tid = int(thread["tid"])
                    lines.append(
                        f"  - {thread['chapter_name'] or thread['display_title'] or thread['raw_title']} "
                        f"(tid={thread_tid}, context={thread_summary_payload(thread).get('resources', {}).get('context')})"
                    )
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        if write_path is not None:
            write_path.parent.mkdir(parents=True, exist_ok=True)
            write_path.write_text(text, encoding="utf-8")
        return text
    finally:
        conn.close()


def generate_series_chapters_json(*, series_id: int, write_path: Path | None = None) -> str:
    from yamibo_mcp.server.schemas import thread_summary_payload

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = SeriesRepository(conn)
        series = repo.get_series(series_id)
        if series is None:
            raise ValueError(f"series not found: {series_id}")
        threads = repo.list_threads_for_series(series_id)
        payload = {
            "series_id": series_id,
            "canonical_title": series["canonical_title"],
            "series_key": series["series_key"],
            "aliases": json.loads(series["aliases_json"] or "[]"),
            "thread_count": len(threads),
            "chapters": [
                {
                    "tid": int(thread["tid"]),
                    "title": thread["display_title"] or thread["raw_title"],
                    "chapter_name": thread["chapter_name"],
                    "chapter_index": thread["chapter_index"],
                    "chapter_index_end": thread.get("chapter_index_end"),
                    "archive_status": thread["archive_status"],
                    "sync_time": thread["sync_time"],
                    "resources": thread_summary_payload(thread, include_export=True)["resources"],
                }
                for thread in threads
            ],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if write_path is not None:
            write_path.parent.mkdir(parents=True, exist_ok=True)
            write_path.write_text(text, encoding="utf-8")
        return text
    finally:
        conn.close()


def _resolve_thread_export_path(settings, paths: StoragePaths, tid: int) -> Path:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        row = ThreadsRepository(conn).get_thread(tid)
    finally:
        conn.close()
    export_value = None if row is None else row["export_path"]
    if not export_value:
        return paths.thread_export_zip(tid)
    export_path = Path(str(export_value))
    return export_path if export_path.is_absolute() else settings.data_dir / export_path


def _build_forums_index_resource(uri: str, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        rows = conn.execute("SELECT * FROM forums ORDER BY forum_id").fetchall()
    finally:
        conn.close()
    forums = [
        {
            "forum_id": row["forum_id"],
            "name": row["name"],
            "content_kind": row["content_kind"],
            "base_url": row["base_url"],
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(forums, ensure_ascii=False, indent=2)}


def _build_forum_summary_resource(uri: str, forum_id: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        row = conn.execute("SELECT * FROM forums WHERE forum_id = ?", (forum_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"uri": uri, "content_type": "application/json", "exists": False, "error": f"forum {forum_id} not found"}
    summary = {
        "forum_id": row["forum_id"],
        "name": row["name"],
        "content_kind": row["content_kind"],
        "base_url": row["base_url"],
        "enabled": bool(row["enabled"]),
    }
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(summary, ensure_ascii=False, indent=2)}


def _build_thread_summary_resource(uri: str, tid: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        thread = repo.get_thread(tid)
        if thread is None:
            return {"uri": uri, "content_type": "application/json", "exists": False, "error": f"thread {tid} not found"}
        title = repo.get_title_parse(tid)
    finally:
        conn.close()
    summary = {
        "tid": thread["tid"],
        "display_title": thread["display_title"] or thread["raw_title"],
        "raw_title": thread["raw_title"],
        "archive_status": thread["archive_status"],
        "validation_status": thread["validation_status"],
        "image_count": thread["image_count"],
        "sync_time": thread["sync_time"],
        "publisher": thread["publisher"],
        "series_id": thread["series_id"],
        "core_title": None if title is None else title["core_title_guess"],
        "series_key": None if title is None else title["series_key"],
        "chapter_name": None if title is None else title["chapter_name"],
        "confidence": None if title is None else title["confidence"],
        "needs_review": None if title is None else bool(title["needs_review"]),
        "resources": {
            "context": thread_context_uri(tid),
            "metadata": thread_metadata_uri(tid),
            "diagnostics": thread_diagnostics_uri(tid),
            "posts": thread_posts_uri(tid),
            "assets": thread_assets_uri(tid),
        },
    }
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(summary, ensure_ascii=False, indent=2)}


def _build_thread_diagnostics_resource(uri: str, tid: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        thread = repo.get_thread(tid)
        if thread is None:
            return {"uri": uri, "content_type": "application/json", "exists": False, "error": f"thread {tid} not found"}
        from yamibo_mcp.db.repositories.assets import AssetsRepository
        assets = AssetsRepository(conn).list_assets(tid)
    finally:
        conn.close()
    missing_urls = json.loads(thread["missing_images_json"] or "[]")
    required_assets = [a for a in assets if a["required"]]
    missing_required = [a for a in required_assets if a["status"] == "missing"]
    warnings: list[str] = []
    next_actions: list[str] = []
    archive_status = thread["archive_status"]
    if archive_status == "stale":
        next_actions.append("archive_thread")
    elif archive_status == "partial":
        warnings.append(f"missing {len(missing_urls)} images")
        next_actions.append("re-export after re-sync")
    elif archive_status == "complete" and not thread["export_path"]:
        next_actions.append("export_thread")
    if missing_required:
        warnings.append(f"missing {len(missing_required)} required assets")
    diagnostics = {
        "tid": tid,
        "archive_status": archive_status,
        "validation_status": thread["validation_status"],
        "image_count": thread["image_count"],
        "missing_image_count": len(missing_urls),
        "required_assets_count": len(required_assets),
        "missing_required_assets_count": len(missing_required),
        "is_exported": bool(thread["is_exported"]),
        "export_path": thread["export_path"],
        "warnings": warnings,
        "next_actions": next_actions,
    }
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(diagnostics, ensure_ascii=False, indent=2)}


def _build_thread_posts_resource(uri: str, tid: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
        thread = ThreadsRepository(conn).get_thread(tid)
        if thread is None:
            return {"uri": uri, "content_type": "application/json", "exists": False, "error": f"thread {tid} not found"}
        blocks = ContentBlocksRepository(conn).list_blocks(tid)
    finally:
        conn.close()
    posts_data = [
        {
            "id": block["id"],
            "tid": block["tid"],
            "pid": block["pid"],
            "order_index": block["order_index"],
            "block_type": block["block_type"],
            "text": block["text"],
            "asset_id": block["asset_id"],
            "metadata": json.loads(block["metadata_json"] or "{}"),
        }
        for block in blocks
    ]
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(posts_data, ensure_ascii=False, indent=2)}


def _build_thread_assets_resource(uri: str, tid: int, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        from yamibo_mcp.db.repositories.assets import AssetsRepository
        thread = ThreadsRepository(conn).get_thread(tid)
        if thread is None:
            return {"uri": uri, "content_type": "application/json", "exists": False, "error": f"thread {tid} not found"}
        assets = AssetsRepository(conn).list_assets(tid)
    finally:
        conn.close()
    assets_data = [
        {
            "asset_id": asset["asset_id"],
            "tid": asset["tid"],
            "pid": asset["pid"],
            "asset_type": asset["asset_type"],
            "remote_url": asset["remote_url"],
            "local_path": asset["local_path"],
            "exportable": bool(asset["exportable"]),
            "required": bool(asset["required"]),
            "status": asset["status"],
        }
        for asset in assets
    ]
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(assets_data, ensure_ascii=False, indent=2)}


def _build_thread_update_check_resource(uri: str, tid: int) -> dict[str, object]:
    from yamibo_mcp.application.update_queries import check_thread_updates

    result = check_thread_updates(tid=tid)
    if result.get("status") == "failed" and result.get("reason") == f"thread {tid} not found":
        return {"uri": uri, "content_type": "application/json", "exists": False, "error": result["reason"]}
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(result, ensure_ascii=False, indent=2)}


def _build_job_events_resource(uri: str, job_id: str, settings) -> dict[str, object]:
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        from yamibo_mcp.db.repositories.job_events import JobEventsRepository
        events = JobEventsRepository(conn).list(job_id=job_id)
    finally:
        conn.close()
    events_data = [
        {
            "event_id": event.event_id,
            "job_id": event.job_id,
            "event_type": event.event_type,
            "status": event.status,
            "stage": event.stage,
            "payload": event.payload,
            "created_at": event.created_at,
        }
        for event in events
    ]
    return {"uri": uri, "content_type": "application/json", "exists": True, "text": json.dumps(events_data, ensure_ascii=False, indent=2)}
