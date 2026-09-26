"""Server-owned snapshots for continuing a daily report in embedded Chat."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.assistant_evidence import AssistantEvidenceRepository
from yamibo_mcp.db.repositories.daily_briefs import DailyBriefsRepository


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
MAX_FROZEN_SOURCES = 200
MAX_REPORT_BODY_CHARS = 24_000
MAX_GAP_REASONS = 50


class DailyBriefRevisionError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 404):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def freeze_daily_report_revision(
    *, revision_id: str, session_id: str, settings: Settings | None = None
) -> dict[str, Any]:
    """Resolve a UUID revision, verify the persisted chat-session owner, and snapshot it."""
    if not isinstance(revision_id, str) or not _UUID.fullmatch(revision_id):
        raise DailyBriefRevisionError("CHAT_INVALID_REQUEST", "report_revision 必须是有效的日报 revision UUID。", 400)
    settings = settings or load_settings()
    conn = connect(settings, bootstrap=False)
    try:
        session_row = conn.execute(
            "SELECT data FROM chat_sessions WHERE id = :id", {"id": session_id}
        ).fetchone()
        if session_row is None:
            raise DailyBriefRevisionError("CHAT_SESSION_NOT_FOUND", "会话不存在。", 404)
        try:
            session = json.loads(session_row["data"])
        except (TypeError, ValueError):
            raise DailyBriefRevisionError("CHAT_SESSION_NOT_FOUND", "会话数据无效。", 404)
        if session.get("deleted"):
            raise DailyBriefRevisionError("CHAT_SESSION_NOT_FOUND", "会话不存在。", 404)
        owner_id = str(session.get("owner_id") or "local")
        revision = DailyBriefsRepository(conn).get_report_revision(revision_id)
        # Do not reveal whether another owner's revision exists.
        if revision is None or str(revision.get("owner_id") or "") != owner_id:
            raise DailyBriefRevisionError("CHAT_DAILY_REPORT_UNAVAILABLE", "日报版本不存在或无权访问。")

        stats = revision.get("stats_receipt_json") or {}
        config = revision.get("config_snapshot_json") or {}
        candidates = stats.get("candidates") or []
        candidate_tids = sorted({
            int(item["tid"]) for item in candidates
            if isinstance(item, dict) and type(item.get("tid")) is int and item["tid"] > 0
        })[:200]
        forum_ids = sorted({
            int(value) for value in (config.get("forum_ids") or [])
            if type(value) is int and value > 0
        })
        sources = []
        for source in revision.get("source_receipts_json") or []:
            if not isinstance(source, dict) or len(sources) >= MAX_FROZEN_SOURCES:
                break
            tid, pid = source.get("tid"), source.get("pid")
            receipt_id = source.get("receipt_id")
            if type(tid) is not int or tid < 1 or type(pid) is not int or pid < 1 or not isinstance(receipt_id, str):
                continue
            candidate_tids.append(tid)
            sources.append({
                "receipt_id": receipt_id[:200],
                "tid": tid,
                "pid": pid,
                "published_at": str(source.get("published_at") or "")[:64],
                "content_hash": str(source.get("content_hash") or "")[:64],
                "truncated": bool(source.get("truncated")),
            })
        candidate_tids = sorted(set(candidate_tids))[:200]
        scope = {
            "source_kind": revision.get("source_kind"),
            "target_day": str(revision.get("target_day")),
            "timezone": str(revision.get("timezone") or "UTC"),
            "start_at": _iso(revision.get("window_start")),
            "end_at": _iso(revision.get("window_end")),
            "forum_ids": forum_ids,
            "tids": candidate_tids,
            "pids": sorted({source["pid"] for source in sources})[:500],
        }
        if not scope["forum_ids"] or not scope["start_at"] or not scope["end_at"]:
            raise DailyBriefRevisionError("CHAT_DAILY_REPORT_SCOPE_INVALID", "日报版本缺少可冻结的期次范围。", 409)

        report = revision.get("report_json") or {}
        body = revision.get("body_markdown") or ""
        if not body and isinstance(report, dict):
            body = _render_fallback(report)
        gaps = revision.get("gap_reasons_json") or []
        coverage = stats.get("coverage") or {}
        return {
            "revision_id": revision_id,
            "issue_id": str(revision["issue_id"]),
            "report_revision": int(revision["report_revision"]),
            "status": str(revision.get("status") or "unknown"),
            "coverage_status": str(revision.get("coverage_status") or "unknown"),
            "created_at": _iso(revision.get("created_at")),
            "scope": scope,
            "body_markdown": str(body)[:MAX_REPORT_BODY_CHARS],
            "body_truncated": len(str(body)) > MAX_REPORT_BODY_CHARS,
            "stats": _bounded_stats(stats),
            "coverage": {
                "coverage_status": coverage.get("coverage_status") or revision.get("coverage_status"),
                "complete": bool(coverage.get("complete")),
                "full_forum_coverage_proven": bool(coverage.get("full_forum_coverage_proven")),
                "local_archived_forum_ids": coverage.get("local_archived_forum_ids", []),
                "excluded_forum_ids": coverage.get("excluded_forum_ids", []),
                "gap_reasons": list(dict.fromkeys(
                    [str(reason)[:500] for reason in (gaps or [])]
                    + [str(reason)[:500] for reason in (coverage.get("gap_reasons") or [])]
                ))[:MAX_GAP_REASONS],
            },
            "sources": sources,
            "source_count_total": len(revision.get("source_receipts_json") or []),
        }
    finally:
        conn.close()


def read_daily_report_snapshot(
    *, snapshot: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    """Read only a snapshot already frozen on this Run; annotate local-source drift."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("revision_id"), str):
        raise DailyBriefRevisionError("CHAT_DAILY_REPORT_UNAVAILABLE", "本次 Run 没有冻结日报版本。")
    settings = settings or load_settings()
    conn = connect(settings, bootstrap=False)
    try:
        evidence = AssistantEvidenceRepository(conn)
        sources = []
        for frozen in snapshot.get("sources", [])[:MAX_FROZEN_SOURCES]:
            item = dict(frozen)
            floor = evidence.read_floor(item["tid"], item["pid"])
            if floor is None:
                item["source_status"] = "source_unavailable"
            else:
                current_hash = hashlib.sha256(str(floor.get("content") or "").encode("utf-8")).hexdigest()
                item["source_status"] = "unchanged" if current_hash == item.get("content_hash") else "source_changed"
            item["url"] = f"https://bbs.yamibo.com/forum.php?mod=redirect&goto=findpost&pid={item['pid']}"
            sources.append(item)
        return {
            "revision_id": snapshot["revision_id"],
            "issue_id": snapshot["issue_id"],
            "report_revision": snapshot["report_revision"],
            "immutable": True,
            "status": snapshot["status"],
            "created_at": snapshot.get("created_at"),
            "scope": snapshot["scope"],
            "body_markdown": snapshot["body_markdown"],
            "body_truncated": snapshot["body_truncated"],
            "stats": snapshot["stats"],
            "coverage": snapshot["coverage"],
            "sources": sources,
            "source_count_total": snapshot["source_count_total"],
            "sources_truncated": snapshot["source_count_total"] > len(sources),
        }
    finally:
        conn.close()


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value is not None else None)


def _bounded_stats(stats: dict[str, Any]) -> dict[str, Any]:
    coverage = stats.get("coverage") or {}
    counts = stats.get("counts") or {}
    return {
        "target_day": stats.get("target_day"),
        "timezone": stats.get("timezone"),
        "window_start_utc": stats.get("window_start_utc"),
        "window_end_utc": stats.get("window_end_utc"),
        "facts_version": stats.get("facts_version"),
        "counts": {str(key): value for key, value in list(counts.items())[:30]},
        "coverage": {
            "coverage_status": coverage.get("coverage_status"),
            "local_archived_forum_ids": list(coverage.get("local_archived_forum_ids") or [])[:100],
            "requested_forum_ids": list(coverage.get("requested_forum_ids") or [])[:100],
            "excluded_forum_ids": list(coverage.get("excluded_forum_ids") or [])[:100],
            "complete": bool(coverage.get("complete")),
            "full_forum_coverage_proven": bool(coverage.get("full_forum_coverage_proven")),
            "gap_reasons": [str(reason)[:500] for reason in (coverage.get("gap_reasons") or [])[:MAX_GAP_REASONS]],
        },
    }


def _render_fallback(report: dict[str, Any]) -> str:
    facts = report.get("facts") or {}
    editorial = report.get("editorial") or {}
    recommendations = editorial.get("recommendations") or []
    return json.dumps(
        {
            "facts": _bounded_stats(facts),
            "recommendations": recommendations[:100] if isinstance(recommendations, list) else [],
        },
        ensure_ascii=False,
        default=str,
    )
