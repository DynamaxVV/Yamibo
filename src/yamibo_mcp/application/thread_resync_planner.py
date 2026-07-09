from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yamibo_mcp.application.contracts import AgentResult
from yamibo_mcp.application.remote_queries import browse_forum_page
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.threads import ThreadsRepository


_DECISION_ORDER = {"force_resync": 0, "needs_resync": 1, "unknown": 2, "maybe_changed": 3, "skip": 4}


def plan_thread_resync_batch(
    *,
    forum_id: int | None = None,
    pages: list[int] | None = None,
    tids: list[int] | None = None,
    order: str = "default",
    mode: str = "daily_delta",
    force: bool = False,
    include_unknown: bool = True,
    max_detail_jobs: int | None = None,
    persist_observation: bool = False,
) -> AgentResult:
    normalized_tids = _normalize_ids(tids)
    normalized_pages = _normalize_ids(pages)
    if not normalized_tids and not normalized_pages:
        raise ValueError("pages or tids required")
    if mode not in {"backfill", "daily_delta"}:
        raise ValueError("unsupported mode")

    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        repo = ThreadsRepository(conn)
        scan_tids = normalized_tids[:]
        scanned_pages: list[int] = []
        page_sources: dict[int, dict[str, Any]] = {}
        if normalized_pages:
            page_tids, scanned_pages, page_sources = _collect_tids_from_pages(
                forum_id=forum_id or 30,
                pages=normalized_pages,
                order=order,
            )
            scan_tids.extend(page_tids)
        scan_tids = _normalize_ids(scan_tids)
        items = repo.probe_archive_states(scan_tids)
        planned = [_plan_item(item, mode=mode, force=force, include_unknown=include_unknown) for item in items]
        planned.sort(key=_sort_key)
        if max_detail_jobs is not None:
            planned = planned[: max(0, max_detail_jobs)]
        return AgentResult(ok=True, data=_serialize_jsonable(_build_report(
            forum_id=forum_id,
            mode=mode,
            order=order,
            items=planned,
            force=force,
            include_unknown=include_unknown,
            persist_observation=persist_observation,
            scanned_pages=scanned_pages,
            scanned_page_sources=page_sources,
            scanned_tids=scan_tids,
        )))
    finally:
        conn.close()


def _build_report(*, forum_id: int | None, mode: str, order: str, items: list[dict[str, Any]], force: bool, include_unknown: bool, persist_observation: bool, scanned_pages: list[int], scanned_page_sources: dict[int, dict[str, Any]], scanned_tids: list[int]) -> dict[str, Any]:
    counts: dict[str, int] = {k: 0 for k in _DECISION_ORDER}
    reason_counts: dict[str, int] = {}
    for item in items:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1
        for reason in item["reason_codes"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "forum_id": forum_id,
        "mode": mode,
        "order": order,
        "force": force,
        "include_unknown": include_unknown,
        "persist_observation": persist_observation,
        "count": len(items),
        "decision_counts": counts,
        "reason_counts": reason_counts,
        "items": items,
        "coverage": {
            "total": len(scanned_tids) or len(items),
            "planned": counts.get("needs_resync", 0) + counts.get("force_resync", 0),
            "scanned_pages": scanned_pages,
            "scanned_page_sources": scanned_page_sources,
            "scanned_tids": scanned_tids,
        },
    }


def _plan_item(item: dict[str, Any], *, mode: str, force: bool, include_unknown: bool) -> dict[str, Any]:
    decision, reason_codes, job_preview = _decide(item, mode=mode, force=force, include_unknown=include_unknown)
    return {
        "tid": item["tid"],
        "decision": decision,
        "reason_codes": reason_codes,
        "remote_observation": {
            "remote_last_reply_at": item.get("remote_last_reply_at"),
            "remote_last_reply_at_raw": item.get("remote_last_reply_at_raw"),
            "remote_last_replier": item.get("remote_last_replier"),
            "remote_reply_count": item.get("remote_reply_count"),
            "remote_observed_at": item.get("remote_observed_at"),
            "remote_observed_from": item.get("remote_observed_from"),
        },
        "local_state": {
            "archive_status": item.get("archive_status"),
            "sync_time": item.get("sync_time"),
            "local_floor_count": item.get("local_floor_count"),
            "local_reply_count": item.get("local_reply_count"),
            "reply_count_mismatch_reason": item.get("reply_count_mismatch_reason"),
        },
        "job_preview": job_preview,
    }


def _decide(item: dict[str, Any], *, mode: str, force: bool, include_unknown: bool) -> tuple[str, list[str], dict[str, Any]]:
    if force:
        return "force_resync", ["force"], {"create_sync_thread": True}
    if not item.get("archived"):
        return "needs_resync", ["not_archived"], {"create_sync_thread": True}
    archive_status = item.get("archive_status")
    if archive_status in {"partial", "failed"}:
        return ("maybe_changed" if mode == "daily_delta" else "needs_resync"), [f"{archive_status}_archive"], {"create_sync_thread": mode == "backfill"}
    remote_reply_count = item.get("remote_reply_count")
    local_reply_count = int(item.get("local_reply_count") or 0)
    remote_last_reply_at = item.get("remote_last_reply_at")
    local_last_floor_pub_time = item.get("local_last_floor_pub_time")
    remote_dt = _parse_dt(remote_last_reply_at)
    local_dt = _parse_dt(local_last_floor_pub_time)
    if remote_dt and local_dt and remote_dt > local_dt:
        return "needs_resync", ["remote_last_reply_newer"], {"create_sync_thread": True}
    if isinstance(remote_reply_count, int) and remote_reply_count > local_reply_count:
        return "needs_resync", ["remote_reply_count_gt_local"], {"create_sync_thread": True}
    if isinstance(remote_reply_count, int) and remote_reply_count < local_reply_count:
        return "unknown", ["reply_count_mismatch"], {"create_sync_thread": include_unknown}
    if remote_reply_count is None and remote_dt is None:
        return ("unknown" if include_unknown else "skip"), ["missing_observation"], {"create_sync_thread": include_unknown}
    if remote_reply_count == local_reply_count and (not remote_dt or not local_dt or remote_dt <= local_dt):
        return "skip", ["up_to_date"], {"create_sync_thread": False}
    return "maybe_changed", ["partial_signal"], {"create_sync_thread": mode == "backfill"}


def _sort_key(item: dict[str, Any]) -> tuple[int, str, int, int]:
    remote = item.get("remote_observation") or {}
    reply_count = remote.get("remote_reply_count")
    reply_key = int(reply_count) if isinstance(reply_count, int) else -10**18
    return (
        _DECISION_ORDER.get(item.get("decision"), 99),
        str(remote.get("remote_last_reply_at") or ""),
        reply_key,
        -int(item.get("tid") or 0),
    )


def _collect_tids_from_pages(*, forum_id: int, pages: list[int], order: str) -> tuple[list[int], list[int], dict[int, dict[str, Any]]]:
    page_tids: list[int] = []
    scanned_pages: list[int] = []
    page_sources: dict[int, dict[str, Any]] = {}
    for page in pages:
        result = browse_forum_page(page=page, forum_id=forum_id, order=order)
        payload = result.data if hasattr(result, "data") and result.data is not None else result
        scanned_pages.append(page)
        page_sources[page] = {
            "forum_url": payload.get("forum_url"),
            "count": payload.get("count"),
            "source": payload.get("source"),
        }
        for item in payload.get("items", []):
            tid = item.get("tid")
            if isinstance(tid, int):
                page_tids.append(tid)
    return page_tids, scanned_pages, page_sources


def _normalize_ids(values: list[int] | None) -> list[int]:
    if not values:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for raw in values:
        value = int(raw)
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _parse_dt(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _serialize_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _serialize_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_serialize_jsonable(v) for v in value]
    return value
