"""Auditable, bounded forum-list coverage scan for manual daily briefs.

Forum-list timestamps are discovery hints only.  The receipt deliberately stays
partial because the list endpoint cannot prove floor-level completeness.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from yamibo_mcp.application.archive_commands import create_thread_archive_job
from yamibo_mcp.application.daily_brief_facts import target_day_window
from yamibo_mcp.application.remote_queries import browse_forum_page
from yamibo_mcp.db.repositories.daily_briefs import DailyBriefsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository

MAX_PAGE_BUDGET = 200
MAX_ARCHIVE_BUDGET = 100


def check_daily_brief_coverage(
    conn,
    *,
    issue_id: str,
    target_day: date,
    forum_ids: list[int] | None = None,
    timezone_name: str = "Asia/Shanghai",
    page_budget: int = 200,
    archive_budget: int = 100,
    archive_mode: str = "text_only",
    browse_page: Callable[..., dict[str, Any]] | None = None,
    enqueue_archive: Callable[..., Any] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Scan creation and reply-activity lists with shared budgets and persist a receipt.

    Dependencies are injectable so unit tests never call the forum or daemon.
    This function creates ordinary archive Jobs but does not wait for them.
    """
    if not isinstance(target_day, date) or isinstance(target_day, datetime):
        raise ValueError("target_day must be a date")
    if not isinstance(page_budget, int) or isinstance(page_budget, bool) or not 1 <= page_budget <= MAX_PAGE_BUDGET:
        raise ValueError(f"page_budget must be between 1 and {MAX_PAGE_BUDGET}")
    if not isinstance(archive_budget, int) or isinstance(archive_budget, bool) or not 1 <= archive_budget <= MAX_ARCHIVE_BUDGET:
        raise ValueError(f"archive_budget must be between 1 and {MAX_ARCHIVE_BUDGET}")
    if archive_mode not in {"text_only", "full"}:
        raise ValueError("archive_mode must be text_only or full")
    start, end = target_day_window(target_day, timezone_name)
    zone = ZoneInfo(timezone_name)
    clock = now or (lambda: datetime.now(timezone.utc))
    browse = browse_page or browse_forum_page
    enqueue = enqueue_archive or create_thread_archive_job

    requested = sorted({int(value) for value in (forum_ids or []) if int(value) > 0})
    rows = conn.execute(
        """SELECT forum_id FROM forums
           WHERE (content_kind = 'discussion' OR (forum_id = 30 AND content_kind = 'comic')) AND enabled IS TRUE
           ORDER BY forum_id"""
    ).fetchall()
    enabled = [int(row["forum_id"]) for row in rows]
    selected = [value for value in enabled if not requested or value in requested]
    excluded = sorted(set(requested) - set(selected))

    pages: list[dict[str, Any]] = []
    candidates: dict[int, dict[str, Any]] = {}
    forum_receipts: list[dict[str, Any]] = []
    remaining = page_budget
    scans = [(forum_id, order) for forum_id in selected for order in ("dateline", "default")]
    for scan_index, (forum_id, order) in enumerate(scans):
        # Reserve a fair share for remaining discovery paths and forums.
        scan_budget = (remaining + len(scans) - scan_index - 1) // (len(scans) - scan_index)
        page = 1
        declared_pages: int | None = None
        stop_reason = "not_started"
        forum_page_receipts: list[dict[str, Any]] = []
        while remaining > 0 and len(forum_page_receipts) < scan_budget:
            try:
                payload = browse(page=page, forum_id=forum_id, order=order)
            except Exception as exc:
                stop_reason = "page_fetch_error"
                forum_page_receipts.append({
                    "page": page, "order": order, "url": None, "fetched_at": _iso_utc(clock()),
                    "oldest_date": None, "newest_date": None,
                    "missing_posted_at": None, "missing_last_reply_at": None,
                    "stop_reason": stop_reason,
                    "page_budget_total": page_budget,
                    "pages_remaining_after_page": remaining - 1,
                    "target_day_start_utc": start.isoformat(),
                    "target_day_end_utc": end.isoformat(),
                    "error": type(exc).__name__,
                })
                pages.append({"forum_id": forum_id, **forum_page_receipts[-1]})
                remaining -= 1
                break

            remaining -= 1
            fetched_at = _iso_utc(clock())
            page_items = payload.get("items") if isinstance(payload.get("items"), list) else []
            dates: list[datetime] = []
            missing_posted = missing_reply = 0
            for item in page_items:
                posted = _parse_timestamp(item.get("posted_at"), zone)
                replied = _parse_timestamp(item.get("last_reply_at"), zone)
                missing_posted += posted is None
                missing_reply += replied is None
                dates.extend(value for value in (posted, replied) if value is not None)
                tid = _positive_int(item.get("tid"))
                if tid is None:
                    continue
                matching = [
                    ("thread_created", posted),
                    ("thread_replied", replied),
                ]
                reasons = [reason for reason, value in matching if value is not None and start <= value < end]
                # A later last reply cannot rule out activity on the target day.
                # Floor verification, not this hint, decides inclusion.
                if replied is not None and replied >= end and (posted is None or posted < end):
                    reasons.append("possible_target_day_activity")
                if reasons:
                    entry = candidates.setdefault(tid, {
                        "tid": tid, "forum_id": forum_id,
                        "title": item.get("display_title") or item.get("raw_title"),
                        "source_pages": [], "source_scans": [], "candidate_reasons": [],
                    })
                    if page not in entry["source_pages"]:
                        entry["source_pages"].append(page)
                    entry["source_scans"].append({"order": order, "page": page})
                    entry["candidate_reasons"] = sorted(set(entry["candidate_reasons"] + reasons))

            page_url = payload.get("forum_url")
            receipt = {
                "page": page,
                "order": order,
                "url": page_url,
                "fetched_at": fetched_at,
                "oldest_date": min(dates).isoformat() if dates else None,
                "newest_date": max(dates).isoformat() if dates else None,
                "missing_posted_at": missing_posted,
                "missing_last_reply_at": missing_reply,
                "item_count": len(page_items),
                "total_pages": _positive_int(payload.get("total_pages")) or 0,
                "stop_reason": None,
            }
            forum_page_receipts.append(receipt)
            pages.append({"forum_id": forum_id, **receipt})
            reported = _positive_int(payload.get("total_pages"))
            if declared_pages is None:
                declared_pages = reported
            elif reported and declared_pages != reported:
                stop_reason = "total_pages_changed_during_scan"
                break
            if not page_items:
                stop_reason = "empty_page"
                break
            if not reported and order == "dateline":
                stop_reason = "total_pages_unavailable"
                break
            if reported and page >= reported:
                stop_reason = "reached_reported_last_page"
                break
            page += 1
        else:
            stop_reason = "page_budget_exhausted" if remaining == 0 else "scan_budget_exhausted"
        if remaining == 0 and stop_reason == "not_started":
            stop_reason = "page_budget_exhausted"
        if stop_reason == "not_started":
            stop_reason = "page_budget_exhausted"
        pages_used_before_forum = page_budget - remaining - len(forum_page_receipts)
        for index, page_receipt in enumerate(forum_page_receipts):
            page_receipt["stop_reason"] = (
                stop_reason if index == len(forum_page_receipts) - 1 else "page_scanned_continue"
            )
            page_receipt["page_budget_total"] = page_budget
            page_receipt["pages_remaining_after_page"] = max(
                page_budget - pages_used_before_forum - index - 1, 0
            )
            page_receipt["target_day_start_utc"] = start.isoformat()
            page_receipt["target_day_end_utc"] = end.isoformat()
            for global_page in pages:
                if global_page["forum_id"] == forum_id and global_page["page"] == page_receipt["page"] and global_page["order"] == order:
                    global_page.update(page_receipt)
                    break
        forum_receipts.append({
            "forum_id": forum_id,
            "order": order,
            "pages_scanned": len(forum_page_receipts),
            "reported_total_pages": declared_pages,
            "stop_reason": stop_reason,
            "pages": forum_page_receipts,
        })
        if remaining <= 0:
            break

    candidate_receipts = []
    ordered_candidates = sorted(candidates.values(), key=lambda item: (item["forum_id"], item["tid"]))
    queued_candidate_count = min(len(ordered_candidates), archive_budget)
    for candidate_index, candidate in enumerate(ordered_candidates):
        if candidate_index >= archive_budget:
            candidate_receipts.append({
                **candidate,
                "archive": {
                    "job_id": None,
                    "job_status": None,
                    "job_terminal": False,
                    "status": "not_queued_budget_exhausted",
                },
            })
            continue
        try:
            result = enqueue(
                tid=candidate["tid"], forum_id=candidate["forum_id"],
                mode=archive_mode, connection=conn,
            )
            data = result.data if hasattr(result, "data") else result
            data = data if isinstance(data, dict) else {}
            job_id = data.get("job_id")
            job_status = data.get("status")
            job_artifacts: dict[str, Any] = {}
            if job_id:
                try:
                    job = JobsRepository(conn).get(str(job_id))
                    job_status = job.status
                    job_artifacts = job.artifacts if isinstance(job.artifacts, dict) else {}
                    terminal = job.status in {"succeeded", "partial", "failed", "cancelled", "dead_letter"}
                except Exception:
                    terminal = False
            else:
                terminal = job_status == "satisfied"
            page_evidence = _archive_job_page_evidence(job_artifacts)
            evidence = _thread_floor_evidence(conn, candidate["tid"], start, end)
            valid = bool(
                evidence.get("eligible_discussion")
                and evidence.get("archive_status") == "complete"
                and evidence.get("floor_count", 0) > 0
                and evidence.get("local_reply_count_consistent") is True
                and evidence.get("target_day_floor_count", 0) > 0
                and terminal
                and job_status == "succeeded"
                and page_evidence["complete"]
            )
            candidate_receipts.append({
                **candidate,
                "archive": {
                    "job_id": job_id,
                    "job_status": job_status,
                    "job_terminal": terminal,
                    "job_page_evidence": page_evidence,
                    "single_thread_integrity": evidence,
                    "target_day_floor_evidence_valid": valid,
                    "status": (
                        "verified" if valid
                        else "not_verifiable" if not page_evidence["complete"]
                        else "pending_or_invalid"
                    ),
                },
            })
        except Exception as exc:
            candidate_receipts.append({
                **candidate,
                "archive": {"job_terminal": False, "status": "enqueue_or_verify_error", "error": type(exc).__name__},
            })

    gaps = ["forum-list timestamps discover candidates but do not prove complete target-day floor coverage",
            "current reply ordering is not a historical snapshot; historical activity requires target-day floor evidence"]
    if excluded:
        gaps.append("requested forum IDs excluded because they are disabled or outside the daily brief forum content policy")
    if len(selected) != len(requested) and requested:
        gaps.append("not all requested forums were scanned")
    if len(forum_receipts) < len(scans) or any(
        item["stop_reason"] != "reached_reported_last_page" for item in forum_receipts
    ):
        gaps.append("one or more selected forums were not fully scanned within this attempt")
    if any(page["missing_posted_at"] or page["missing_last_reply_at"] for page in pages):
        gaps.append("one or more list timestamps were missing and could not be parsed")
    if any(item["stop_reason"] not in {"reached_reported_last_page"} for item in forum_receipts):
        gaps.append("one or more forum scans did not reach a stable reported last page")
    if any(item["archive"]["status"] == "not_verifiable" for item in candidate_receipts):
        gaps.append("one or more candidate jobs lack sufficient page-count and last-page artifacts to prove archive completeness")
    if any(item["archive"]["status"] == "pending_or_invalid" for item in candidate_receipts):
        gaps.append("one or more candidate archive jobs are not yet terminal or their local floor evidence is invalid")
    if any(item["archive"]["status"] == "enqueue_or_verify_error" for item in candidate_receipts):
        gaps.append("one or more candidate archive jobs could not be queued or inspected")
    if len(ordered_candidates) > archive_budget:
        gaps.append("candidate archive budget exhausted; remaining candidates were recorded but not queued")

    receipt = {
        "coverage_status": "local_snapshot_only" if not pages else "partial",
        "complete": False,
        "full_forum_coverage_proven": False,
        "target_day": target_day.isoformat(),
        "timezone": timezone_name,
        "target_day_start_utc": start.isoformat(),
        "target_day_end_utc": end.isoformat(),
        "page_budget": page_budget,
        "archive_budget": archive_budget,
        "pages_used": page_budget - remaining,
        "pages_remaining": remaining,
        "archive_mode": archive_mode,
        "requested_forum_ids": requested or None,
        "enabled_discussion_forum_ids": enabled,
        "scanned_forum_ids": sorted({item["forum_id"] for item in forum_receipts}),
        "discovery_orders": ["dateline", "default"],
        "excluded_forum_ids": excluded,
        "forums": forum_receipts,
        "pages": pages,
        "candidate_count": len(candidate_receipts),
        "candidate_queue_count": queued_candidate_count,
        "candidate_queue_truncated": len(ordered_candidates) > archive_budget,
        "candidates": candidate_receipts,
        "gap_reasons": gaps,
    }
    attempt = DailyBriefsRepository(conn).start_attempt(issue_id=issue_id)
    DailyBriefsRepository(conn).finish_attempt(
        attempt_id=attempt["attempt_id"], status="partial", coverage=receipt,
    )
    return {"attempt_id": attempt["attempt_id"], **receipt}


def _archive_job_page_evidence(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Require fetch completeness evidence from the terminal archive Job itself."""
    pages_fetched = _positive_int(artifacts.get("pages_fetched"))
    total_pages = _positive_int(
        artifacts.get("total_pages_detected") or artifacts.get("remote_total_pages")
    )
    stopped_reason = artifacts.get("fetch_stopped_reason") or artifacts.get("stopped_reason")
    complete = bool(
        pages_fetched is not None
        and total_pages is not None
        and pages_fetched >= total_pages
        and stopped_reason == "last_page"
        and artifacts.get("archive_status") == "complete"
    )
    return {
        "pages_fetched": pages_fetched,
        "total_pages": total_pages,
        "stopped_reason": stopped_reason,
        "archive_status": artifacts.get("archive_status"),
        "complete": complete,
    }


def _thread_floor_evidence(conn, tid: int, start: datetime, end: datetime) -> dict[str, Any]:
    row = conn.execute(
        """SELECT t.archive_status, t.capture_mode, t.local_reply_count, t.reply_count_mismatch_reason,
                  (((t.content_kind = 'discussion' AND f.content_kind = 'discussion')
                    OR (f.forum_id = 30 AND t.content_kind = 'comic' AND f.content_kind = 'comic')) AND f.enabled IS TRUE) AS eligible_discussion,
                  COUNT(DISTINCT floor.pid) AS floor_count,
                  COUNT(DISTINCT CASE WHEN floor.pub_time >= :start AND floor.pub_time < :end THEN floor.pid END) AS target_day_floor_count,
                  MIN(floor.pub_time) AS oldest_floor_at,
                  MAX(floor.pub_time) AS newest_floor_at
           FROM threads t
           JOIN forums f ON f.forum_id = t.forum_id
           LEFT JOIN floors floor ON floor.tid = t.tid
           WHERE t.tid = :tid
           GROUP BY t.archive_status, t.capture_mode, t.local_reply_count,
                    t.reply_count_mismatch_reason, t.content_kind, f.content_kind, f.forum_id, f.enabled""",
        {"tid": tid, "start": start, "end": end},
    ).fetchone()
    if row is None:
        return {"eligible_discussion": False, "floor_count": 0, "target_day_floor_count": 0}
    return {
        "eligible_discussion": bool(row["eligible_discussion"]),
        "archive_status": row["archive_status"],
        "capture_mode": row["capture_mode"],
        "floor_count": int(row["floor_count"] or 0),
        "local_reply_count": row["local_reply_count"],
        "reply_count_mismatch_reason": row["reply_count_mismatch_reason"],
        "local_reply_count_consistent": (
            row["reply_count_mismatch_reason"] is None
            and int(row["local_reply_count"] or 0) == max(int(row["floor_count"] or 0) - 1, 0)
        ),
        "target_day_floor_count": int(row["target_day_floor_count"] or 0),
        "oldest_floor_at": _iso_utc(_parse_timestamp(row["oldest_floor_at"], timezone.utc)) if row["oldest_floor_at"] else None,
        "newest_floor_at": _iso_utc(_parse_timestamp(row["newest_floor_at"], timezone.utc)) if row["newest_floor_at"] else None,
    }


def _parse_timestamp(value: Any, zone: ZoneInfo | timezone) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
