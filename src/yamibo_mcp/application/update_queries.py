from __future__ import annotations

import json
from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.forums import resolve_forum
from yamibo_mcp.domain.thread_fingerprint import floor_content_hash
from yamibo_mcp.server.schemas import thread_summary_payload
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.thread_detail import extract_author_only_total_pages, parse_thread_snapshot
from yamibo_mcp.yamibo.urls import thread_author_url_from_tid


def check_thread_updates(*, tid: int, base_url: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        repo = ThreadsRepository(conn)
        thread = repo.get_thread(tid)
        if thread is None:
            return {
                "tid": tid,
                "status": "failed",
                "reason": f"thread {tid} not found",
                "local_snapshot": None,
                "remote_snapshot": None,
                "evidence": [],
            }

        forum_id = thread["forum_id"] if "forum_id" in thread.keys() else None
        content_kind = thread["content_kind"] if "content_kind" in thread.keys() else None
        forum = resolve_forum(forum_id) if forum_id is not None else None
        if content_kind != "novel" and (forum is None or forum.content_kind != "novel"):
            return {
                "tid": tid,
                "status": "not_supported",
                "reason": f"thread {tid} is not a novel forum thread",
                "local_snapshot": _build_local_snapshot(repo, conn, tid, thread),
                "remote_snapshot": None,
                "evidence": [],
                "thread": thread_summary_payload(thread, include_export=True),
            }

        local_snapshot = _build_local_snapshot(repo, conn, tid, thread)
        local_signature = local_snapshot.get("archive_signature") or {}
        if local_signature.get("last_pid") is None or local_signature.get("last_floor_hash") is None:
            return {
                "tid": tid,
                "status": "unknown",
                "reason": "missing local archive signature",
                "local_snapshot": local_snapshot,
                "remote_snapshot": None,
                "evidence": [],
                "thread": thread_summary_payload(thread, include_export=True),
            }
        author_uid = local_snapshot["author_uid"] or (local_snapshot.get("archive_signature") or {}).get("author_uid")
        if not author_uid:
            return {
                "tid": tid,
                "status": "unknown",
                "reason": "missing author_uid for novel thread",
                "local_snapshot": local_snapshot,
                "remote_snapshot": None,
                "evidence": [],
                "thread": thread_summary_payload(thread, include_export=True),
            }

        client = YamiboClient(
            cookie_file=str(settings.cookie_file),
            use_system_proxy=settings.use_system_proxy,
            login_username=settings.login_username,
            login_password=settings.login_password,
            request_interval=settings.request_interval_seconds,
            request_interval_jitter=settings.request_interval_jitter_seconds,
        )
        resolved_base_url = base_url or (forum.base_url if forum is not None else None) or "https://bbs.yamibo.com"
        author_only_url = thread_author_url_from_tid(tid, author_uid=str(author_uid), base_url=resolved_base_url)

        first_page = client.fetch_thread_page(tid=tid, page=1, author_uid=str(author_uid), base_url=resolved_base_url)
        first_snapshot = parse_thread_snapshot(first_page.html, url=first_page.final_url, tid=tid)
        total_pages = extract_author_only_total_pages(first_page.html, tid=tid, author_uid=str(author_uid))
        if total_pages is None:
            if first_snapshot.floors:
                total_pages = 1
            else:
                return {
                    "tid": tid,
                    "status": "unknown",
                    "reason": "unable to determine author-only pagination",
                    "local_snapshot": local_snapshot,
                    "remote_snapshot": {
                        "author_only_url": author_only_url,
                        "page_1_url": first_page.final_url,
                    },
                    "evidence": [],
                    "thread": thread_summary_payload(thread, include_export=True),
                }

        last_page = first_page if total_pages == 1 else client.fetch_thread_page(
            tid=tid,
            page=total_pages,
            author_uid=str(author_uid),
            base_url=resolved_base_url,
        )
        last_snapshot = parse_thread_snapshot(last_page.html, url=last_page.final_url, tid=tid)
        remote_last_floor = last_snapshot.floors[-1] if last_snapshot.floors else None
        if remote_last_floor is None:
            return {
                "tid": tid,
                "status": "unknown",
                "reason": "remote last author-only page has no floors",
                "local_snapshot": local_snapshot,
                "remote_snapshot": {
                    "author_only_url": author_only_url,
                    "page_1_url": first_page.final_url,
                    "last_page_url": last_page.final_url,
                    "total_pages": total_pages,
                },
                "evidence": [],
                "thread": thread_summary_payload(thread, include_export=True),
            }

        remote_snapshot = {
            "author_only_url": author_only_url,
            "page_1_url": first_page.final_url,
            "last_page_url": last_page.final_url,
            "total_pages": total_pages,
            "last_pid": remote_last_floor.pid,
            "last_floor_hash": floor_content_hash(remote_last_floor.content),
            "last_floor_no": remote_last_floor.floor_no,
        }

        evidence = _build_evidence(local_snapshot, remote_snapshot)
        if any(item.get("changed") for item in evidence):
            status = "updated"
            reason = "remote author-only snapshot differs from local archive"
        else:
            status = "up_to_date"
            reason = "remote author-only snapshot matches local archive"

        return {
            "tid": tid,
            "status": status,
            "reason": reason,
            "local_snapshot": local_snapshot,
            "remote_snapshot": remote_snapshot,
            "evidence": evidence,
            "thread": thread_summary_payload(thread, include_export=True),
        }
    except Exception as exc:
        return {
            "tid": tid,
            "status": "failed",
            "reason": str(exc),
            "local_snapshot": None,
            "remote_snapshot": None,
            "evidence": [],
        }
    finally:
        conn.close()


def _build_local_snapshot(repo: ThreadsRepository, conn, tid: int, thread) -> dict[str, Any]:
    floors = repo.list_floors(tid)
    title_row = repo.get_title_parse(tid)
    author_uid = thread["publisher_uid"] if "publisher_uid" in thread.keys() else None
    current_last_floor = floors[-1] if floors else None
    current_signature = None
    if current_last_floor is not None:
        current_signature = {
            "last_pid": current_last_floor["pid"],
            "last_floor_hash": floor_content_hash(current_last_floor["content"] if "content" in current_last_floor.keys() else None),
            "floor_count": len(floors),
        }
    archived_signature = _latest_archive_signature(conn, tid)
    archive_signature = {
        **(current_signature or {}),
        **(archived_signature or {}),
    }
    archive_signature = {
        **archive_signature,
        "source": "job_artifact" if archived_signature else "computed",
    }
    return {
        "author_uid": author_uid or archive_signature.get("author_uid"),
        "archive_status": thread["archive_status"] if "archive_status" in thread.keys() else None,
        "sync_time": thread["sync_time"] if "sync_time" in thread.keys() else None,
        "floor_count": len(floors),
        "last_pid": None if current_last_floor is None else current_last_floor["pid"],
        "last_floor_hash": None if current_last_floor is None else floor_content_hash(current_last_floor["content"] if "content" in current_last_floor.keys() else None),
        "archive_signature": archive_signature,
        "title": None if title_row is None else {
            "raw_title": title_row["raw_title"],
            "display_title": title_row["display_title"],
            "core_title_guess": title_row["core_title_guess"],
            "series_key": title_row["series_key"],
        },
    }


def _latest_archive_signature(conn, tid: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT artifacts_json
        FROM jobs
        WHERE job_type = 'sync_thread'
          AND tid = ?
          AND status IN ('succeeded', 'partial')
        ORDER BY created_at DESC, job_id DESC
        LIMIT 1
        """,
        (tid,),
    ).fetchone()
    if row is None:
        return None
    try:
        artifacts = json.loads(row["artifacts_json"] or "{}")
    except ValueError:
        return None
    signature = artifacts.get("archive_signature")
    if isinstance(signature, dict):
        return signature
    fallback = {
        key: artifacts.get(key)
        for key in ("author_uid", "last_pid", "floor_count", "last_floor_hash", "author_only_total_pages", "total_pages_detected")
        if artifacts.get(key) is not None
    }
    return fallback or None


def _build_evidence(local_snapshot: dict[str, Any], remote_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    local_signature = local_snapshot.get("archive_signature") or {}
    if local_signature.get("last_pid") is not None and remote_snapshot.get("last_pid") is not None:
        evidence.append(
            {
                "field": "last_pid",
                "local": local_signature.get("last_pid"),
                "remote": remote_snapshot.get("last_pid"),
                "changed": local_signature.get("last_pid") != remote_snapshot.get("last_pid"),
            }
        )
    if local_signature.get("last_floor_hash") is not None and remote_snapshot.get("last_floor_hash") is not None:
        evidence.append(
            {
                "field": "last_floor_hash",
                "local": local_signature.get("last_floor_hash"),
                "remote": remote_snapshot.get("last_floor_hash"),
                "changed": local_signature.get("last_floor_hash") != remote_snapshot.get("last_floor_hash"),
            }
        )
    local_total_pages = local_signature.get("author_only_total_pages") or local_signature.get("total_pages_detected")
    if local_total_pages is not None and remote_snapshot.get("total_pages") is not None:
        evidence.append(
            {
                "field": "total_pages",
                "local": local_total_pages,
                "remote": remote_snapshot.get("total_pages"),
                "changed": local_total_pages != remote_snapshot.get("total_pages"),
            }
        )
    return evidence
