"""Server-owned frozen discussion scopes and auditable source-read receipts.

This module is an application-service seam for trusted Run orchestration. It is
not registered as an MCP tool or HTTP route: model/client input must never call
``freeze_discussion_scope`` or supply scope contents directly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Sequence

from yamibo_mcp.application.contracts import AgentError, AgentResult
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.assistant_evidence import AssistantEvidenceRepository
from yamibo_mcp.db.repositories.discussion_search import parse_date_bound

MAX_SOURCE_BYTES = 16_384
DEFAULT_SOURCE_BYTES = 8_000
MAX_SCOPE_TIDS = 200
MAX_SCOPE_PIDS = 500


def freeze_discussion_scope(
    *,
    run_id: str,
    mode: str,
    forum_ids: Sequence[int] | None = None,
    tids: Sequence[int] | None = None,
    pids: Sequence[int] | None = None,
    start_at: str | date | datetime | None = None,
    end_at: str | date | datetime | None = None,
) -> AgentResult:
    """Freeze trusted orchestration policy onto an existing embedded-chat Run.

    Callers must derive arguments from server-validated Run/session state. The
    owner is derived from the persisted session, never accepted from the model.
    """
    if not run_id or mode not in {"discovery", "selected"}:
        return _error("INVALID_ARGUMENT", "A Run and valid discussion scope mode are required.")
    try:
        selected_forums = _positive_ids(forum_ids)
        selected_tids = _positive_ids(tids)
        selected_pids = _positive_ids(pids)
        if len(selected_tids) > MAX_SCOPE_TIDS:
            return _error("SCOPE_TOO_BROAD", f"At most {MAX_SCOPE_TIDS} thread IDs may be frozen.")
        if len(selected_pids) > MAX_SCOPE_PIDS:
            return _error("SCOPE_TOO_BROAD", f"At most {MAX_SCOPE_PIDS} floor IDs may be frozen.")
        start_bound = parse_date_bound(start_at)
        end_bound = parse_date_bound(end_at, end=True)
        if start_bound is not None and end_bound is not None and start_bound >= end_bound:
            return _error("INVALID_ARGUMENT", "start_at must be earlier than end_at.")
    except (TypeError, ValueError):
        return _error("INVALID_ARGUMENT", "Scope IDs and dates must be valid positive IDs and ISO dates.")
    if mode == "selected" and not selected_tids:
        return _error("INVALID_ARGUMENT", "Selected scope requires at least one TID.")
    if selected_pids and not selected_tids:
        return _error("INVALID_ARGUMENT", "PID scope requires explicit TIDs.")

    conn = connect(load_settings().db_path)
    try:
        repo = AssistantEvidenceRepository(conn)
        run_row = conn.execute("SELECT parent_id, data FROM chat_runs WHERE id = ?", (run_id,)).fetchone()
        if run_row is None:
            return _error("RUN_NOT_FOUND", "The chat Run does not exist.")
        run = json.loads(run_row["data"])
        session_id = str(run_row["parent_id"] or "")
        if str(run.get("parent_id") or "") != session_id:
            return _error("RUN_SESSION_MISMATCH", "The Run record no longer matches its persisted session.")
        session = _chat_record(conn, "sessions", session_id)
        if session is None or session.get("deleted"):
            return _error("SESSION_NOT_FOUND", "The Run session does not exist.")
        owner_id = str(session.get("owner_id") or "local")
        allowed_forums = _enabled_discussion_forums(repo)
        if selected_forums and set(selected_forums) - set(allowed_forums):
            return _error("INVALID_FORUM_SCOPE", "Only enabled discussion forums may be frozen.")
        if not selected_forums:
            selected_forums = allowed_forums
        existing = conn.execute(
            "SELECT scope_id, owner_id, session_id, run_id, mode, forum_ids, tids, pids, start_at, end_at FROM chat_discussion_scopes WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        canonical = {
            "owner_id": owner_id,
            "session_id": session_id,
            "run_id": run_id,
            "mode": mode,
            "forum_ids": sorted(selected_forums),
            "tids": sorted(selected_tids),
            "pids": sorted(selected_pids),
            "start_at": start_bound.isoformat() if start_bound else None,
            "end_at": end_bound.isoformat() if end_bound else None,
        }
        if existing is not None:
            current = dict(existing.items())
            for key in ("forum_ids", "tids", "pids"):
                current[key] = json.loads(current[key])
            if any(current.get(key) != value for key, value in canonical.items()):
                return _error("SCOPE_CONFLICT", "This Run already has a different frozen scope.")
            return AgentResult(ok=True, data={"scope_id": current["scope_id"], **canonical})

        if selected_tids:
            rows = conn.execute(
                f"SELECT tid, forum_id, content_kind FROM threads WHERE tid IN ({', '.join('?' for _ in selected_tids)})",
                selected_tids,
            ).fetchall()
            valid_tids = {
                int(row["tid"])
                for row in rows
                if row["content_kind"] == "discussion" and int(row["forum_id"] or 0) in selected_forums
            }
            if valid_tids != set(selected_tids):
                return _error("INVALID_THREAD_SCOPE", "Every frozen TID must be a local discussion in the selected forums.")
        if selected_pids:
            pid_rows = conn.execute(
                f"SELECT pid, tid FROM floors WHERE pid IN ({', '.join('?' for _ in selected_pids)})",
                selected_pids,
            ).fetchall()
            valid_pids = {
                int(row["pid"])
                for row in pid_rows
                if int(row["tid"]) in set(selected_tids)
            }
            if valid_pids != set(selected_pids):
                return _error("INVALID_PID_SCOPE", "Every frozen PID must belong to one of the frozen TIDs.")

        scope_id = repo.new_id()
        scope = {
            "scope_id": scope_id,
            **canonical,
            "created_at": repo.now(),
        }
        repo.insert_scope(scope)
        conn.commit()
        return AgentResult(ok=True, data={"scope_id": scope_id, **canonical})
    except Exception:
        return _error("SCOPE_PERSIST_FAILED", "The frozen discussion scope could not be saved.", retryable=True)
    finally:
        conn.close()


def read_discussion_source(
    *,
    scope_id: str,
    run_id: str,
    tid: int,
    pid: int,
    paragraph_start: int | None = None,
    paragraph_end: int | None = None,
    max_bytes: int = DEFAULT_SOURCE_BYTES,
) -> AgentResult:
    """Read one exact PID within its Run's persisted scope and issue a receipt."""
    if not scope_id or not run_id or not _positive_id(tid) or not _positive_id(pid):
        return _error("INVALID_ARGUMENT", "scope_id, run_id, TID and PID are required.")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SOURCE_BYTES:
        return _error("INVALID_ARGUMENT", f"max_bytes must be between 1 and {MAX_SOURCE_BYTES}.")
    if paragraph_start is not None and (type(paragraph_start) is not int or paragraph_start < 1):
        return _error("INVALID_ARGUMENT", "paragraph_start must be a positive integer.")
    if paragraph_end is not None and (type(paragraph_end) is not int or paragraph_end < 1):
        return _error("INVALID_ARGUMENT", "paragraph_end must be a positive integer.")
    if paragraph_start and paragraph_end and paragraph_end < paragraph_start:
        return _error("INVALID_ARGUMENT", "paragraph_end must be greater than or equal to paragraph_start.")

    conn = connect(load_settings().db_path)
    try:
        repo = AssistantEvidenceRepository(conn)
        scope = repo.get_scope(scope_id)
        if scope is None or scope["run_id"] != run_id:
            return _error("SCOPE_NOT_FOUND", "The frozen scope does not belong to this Run.")
        run = _chat_record(conn, "runs", run_id)
        session = _chat_record(conn, "sessions", scope["session_id"])
        if (
            run is None
            or session is None
            or run.get("parent_id") != scope["session_id"]
            or session.get("deleted")
            or str(session.get("owner_id") or "local") != scope["owner_id"]
        ):
            return _error("SCOPE_ACCESS_DENIED", "The Run no longer matches its scope owner and session.")
        if scope["tids"] and tid not in scope["tids"]:
            return _error("OUT_OF_SCOPE", "This TID is outside the frozen scope.")
        if scope["pids"] and pid not in scope["pids"]:
            return _error("OUT_OF_SCOPE", "This PID is outside the frozen scope.")

        floor = repo.read_floor(tid, pid, metadata_only=True)
        if floor is None:
            return _error("SOURCE_NOT_FOUND", "The exact archived PID is not available.")
        forum_id = int(floor["forum_id"] or 0)
        if (
            floor["content_kind"] != "discussion"
            or forum_id not in scope["forum_ids"]
            or not repo.has_discussion_forum(forum_id)
        ):
            return _error("OUT_OF_SCOPE", "The source is not in an enabled frozen discussion forum.")
        floor_time = _as_utc_datetime(floor["pub_time"])
        start_time = _as_utc_datetime(scope["start_at"])
        end_time = _as_utc_datetime(scope["end_at"])
        if floor_time is None and (start_time is not None or end_time is not None):
            return _error("SOURCE_TIME_UNKNOWN", "The source floor has no publication time for this dated scope.")
        if start_time is not None and floor_time is not None and floor_time < start_time:
            return _error("OUT_OF_SCOPE", "The source floor is earlier than the frozen date range.")
        if end_time is not None and floor_time is not None and floor_time >= end_time:
            return _error("OUT_OF_SCOPE", "The source floor is later than the frozen date range.")
        scope_created = datetime.fromtimestamp(scope["created_at"], tz=timezone.utc)
        sync_time = _as_utc_datetime(floor["sync_time"])
        if sync_time is not None and sync_time > scope_created:
            return _error("SOURCE_CHANGED", "The archived thread changed after this scope was frozen; refresh the Run scope.")

        verified_floor = repo.read_floor(tid, pid, expected_metadata=floor)
        if verified_floor is None:
            return _error("SOURCE_CHANGED", "The source metadata changed during the scoped read.")
        floor = verified_floor
        content = str(floor["content"] or "")
        content_hash = _source_content_hash(content)
        paragraphs = content.splitlines() or [content]
        first = paragraph_start or 1
        last = paragraph_end or len(paragraphs)
        if first > len(paragraphs):
            return _error("PARAGRAPH_RANGE_NOT_FOUND", "paragraph_start is beyond the source content.")
        last = min(last, len(paragraphs))
        bounded_content, shown_end, partial_paragraph, truncated = _bounded_paragraphs(
            paragraphs[first - 1 : last], first=first, max_bytes=max_bytes
        )
        receipt = {
            "receipt_id": repo.new_id(),
            "scope_id": scope_id,
            "owner_id": scope["owner_id"],
            "session_id": scope["session_id"],
            "run_id": run_id,
            "tid": tid,
            "pid": pid,
            "floor_no": int(floor["floor_no"]),
            "content_hash": content_hash,
            "paragraph_start": first,
            "paragraph_end": shown_end,
            "partial_paragraph": partial_paragraph,
            "content": bounded_content,
            "truncated": truncated,
            "created_at": repo.now(),
        }
        repo.insert_receipt(receipt)
        conn.commit()
        return AgentResult(
            ok=True,
            data={
                "receipt_id": receipt["receipt_id"],
                "scope_id": scope_id,
                "tid": tid,
                "pid": pid,
                "floor_no": receipt["floor_no"],
                "pub_time": floor["pub_time"],
                "content_hash": content_hash,
                "paragraph_range": {"start": receipt["paragraph_start"], "end": receipt["paragraph_end"]},
                "partial_paragraph": receipt["partial_paragraph"],
                "content": bounded_content,
                "truncated": truncated,
                "source_changed_after_scope": False,
            },
        )
    except Exception:
        return _error("SOURCE_READ_FAILED", "The scoped source could not be read.", retryable=True)
    finally:
        conn.close()


def validate_source_receipts(*, run_id: str, receipt_ids: Sequence[str], settings=None) -> AgentResult:
    """Validate citations solely against receipts persisted by this Run."""
    if not run_id or not isinstance(receipt_ids, Sequence) or isinstance(receipt_ids, (str, bytes)):
        return _error("INVALID_ARGUMENT", "run_id and a list of receipt IDs are required.")
    ids = list(dict.fromkeys(item for item in receipt_ids if isinstance(item, str) and item))
    if len(ids) != len(receipt_ids):
        return _error("INVALID_ARGUMENT", "Every receipt ID must be a non-empty string.")
    conn = connect(settings if settings is not None else load_settings().db_path)
    try:
        repo = AssistantEvidenceRepository(conn)
        run = _chat_record(conn, "runs", run_id)
        if run is None:
            return _error("RUN_NOT_FOUND", "The chat Run does not exist.")
        session_id = str(run.get("parent_id") or "")
        session = _chat_record(conn, "sessions", session_id)
        owner_id = str((session or {}).get("owner_id") or "local")
        rows = repo.get_receipts(run_id, ids)
        valid = []
        scopes = {}
        for receipt in rows:
            scope = repo.get_scope(receipt["scope_id"])
            if (
                session is not None and not session.get("deleted")
                and receipt["run_id"] == run_id
                and receipt["session_id"] == session_id and receipt["owner_id"] == owner_id
                and scope is not None and scope["run_id"] == run_id
                and scope["session_id"] == session_id and scope["owner_id"] == owner_id
            ):
                valid.append(receipt)
                scopes[receipt["scope_id"]] = scope
        found = {r["receipt_id"] for r in valid}
        by_id = {r["receipt_id"]: r for r in valid}
        changed: list[str] = []
        for receipt_id, receipt in by_id.items():
            scope = scopes[receipt["scope_id"]]
            tid, pid = int(receipt["tid"]), int(receipt["pid"])
            if (scope["tids"] and tid not in scope["tids"]) or (scope["pids"] and pid not in scope["pids"]):
                changed.append(receipt_id)
                continue
            # Recheck scope metadata before loading any source body. A move or
            # publication-time correction can invalidate unchanged source text.
            metadata = repo.read_floor(tid, pid, metadata_only=True)
            if (
                metadata is None
                or metadata["content_kind"] != "discussion"
                or int(metadata["forum_id"] or 0) not in scope["forum_ids"]
                or not repo.has_discussion_forum(int(metadata["forum_id"] or 0))
            ):
                changed.append(receipt_id)
                continue
            floor_time = _as_utc_datetime(metadata["pub_time"])
            start_time = _as_utc_datetime(scope["start_at"])
            end_time = _as_utc_datetime(scope["end_at"])
            sync_time = _as_utc_datetime(metadata["sync_time"])
            if (
                (floor_time is None and (start_time is not None or end_time is not None))
                or (floor_time is not None and start_time is not None and floor_time < start_time)
                or (floor_time is not None and end_time is not None and floor_time >= end_time)
                or (sync_time is not None and sync_time > datetime.fromtimestamp(scope["created_at"], tz=timezone.utc))
            ):
                changed.append(receipt_id)
                continue
            floor = repo.read_floor(tid, pid, expected_metadata=metadata)
            if (
                floor is None
                or _source_content_hash(str(floor["content"] or "")) != receipt["content_hash"]
                or int(floor["floor_no"] or 0) != int(receipt["floor_no"])
                or floor["content_kind"] != "discussion"
                or not repo.has_discussion_forum(int(floor["forum_id"] or 0))
            ):
                changed.append(receipt_id)
        invalid = set(changed)
        sources = [
            {
                "receipt_id": receipt["receipt_id"], "tid": int(receipt["tid"]),
                "pid": int(receipt["pid"]), "content": receipt["content"],
                "paragraph_range": {"start": receipt["paragraph_start"], "end": receipt["paragraph_end"]},
                "truncated": bool(receipt["truncated"]),
                "source_url": f"/threads/{int(receipt['tid'])}#pid-{int(receipt['pid'])}",
            }
            for receipt_id in ids
            if (receipt := by_id.get(receipt_id)) is not None and receipt_id not in invalid
        ]
        missing = [receipt_id for receipt_id in ids if receipt_id not in found or receipt_id in invalid]
        return AgentResult(
            ok=not missing,
            data={"valid": not missing, "receipt_ids": sorted(found - invalid), "missing_receipt_ids": [item for item in missing if item not in changed], "changed_receipt_ids": changed, "sources": sources},
            error=None if not missing else AgentError(
                code="SOURCE_CHANGED" if changed else "RECEIPT_NOT_READ",
                message="A cited source changed after it was read, or was not read by this Run.",
                agent_hint="Re-read the current source within the frozen Run scope before citing it.",
            ),
        )
    except Exception:
        return _error("RECEIPT_VALIDATION_FAILED", "Source receipt validation failed.", retryable=True)
    finally:
        conn.close()


def list_run_source_receipts(*, run_id: str, limit: int = 100, offset: int = 0) -> AgentResult:
    """List only this persisted Run's receipts and verify each current source.

    A local URL is emitted only when the receipt still identifies the same
    discussion floor. The SPA's stable local anchor is ``#pid-{pid}``; floor
    number is also checked because floor numbering can change after resync.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        return _error("INVALID_ARGUMENT", "run_id is required.")
    if type(limit) is not int or not 1 <= limit <= 500:
        return _error("INVALID_ARGUMENT", "limit must be between 1 and 500.")
    if type(offset) is not int or offset < 0:
        return _error("INVALID_ARGUMENT", "offset must be a non-negative integer.")

    conn = connect(load_settings().db_path)
    try:
        repo = AssistantEvidenceRepository(conn)
        run_row = conn.execute("SELECT parent_id, data FROM chat_runs WHERE id = ?", (run_id,)).fetchone()
        if run_row is None:
            return _error("RUN_NOT_FOUND", "The chat Run does not exist.")
        run = json.loads(run_row["data"])
        session_id = str(run_row["parent_id"] or "")
        if str(run.get("parent_id") or "") != session_id:
            return _error("RUN_SESSION_MISMATCH", "The Run record no longer matches its persisted session.")
        session = _chat_record(conn, "sessions", session_id)
        if session is None or session.get("deleted"):
            return _error("SESSION_NOT_FOUND", "The Run session does not exist.")
        owner_id = str(session.get("owner_id") or "local")
        receipts, total = repo.list_receipts(run_id, limit=limit, offset=offset)
        items = []
        for receipt in receipts:
            scope = repo.get_scope(str(receipt["scope_id"]))
            if (
                receipt["session_id"] != session_id
                or receipt["owner_id"] != owner_id
                or scope is None
                or scope["run_id"] != run_id
                or scope["session_id"] != session_id
                or scope["owner_id"] != owner_id
            ):
                return _error("RECEIPT_ACCESS_DENIED", "A source receipt no longer matches this Run owner and session.")
            item = {
                "receipt_id": receipt["receipt_id"],
                "scope_id": receipt["scope_id"],
                "tid": int(receipt["tid"]),
                "pid": int(receipt["pid"]),
                "floor_no": int(receipt["floor_no"]),
                "content_hash": receipt["content_hash"],
                "paragraph_range": {
                    "start": int(receipt["paragraph_start"]),
                    "end": int(receipt["paragraph_end"]),
                },
                "partial_paragraph": receipt["partial_paragraph"],
                "content": receipt["content"],
                "truncated": bool(receipt["truncated"]),
                "created_at": receipt["created_at"],
                "status": "valid",
                "source_url": None,
            }
            floor = repo.read_floor(int(receipt["tid"]), int(receipt["pid"]))
            if floor is None:
                item["status"] = "source_missing"
            elif (
                _source_content_hash(str(floor["content"] or "")) != receipt["content_hash"]
                or int(floor["floor_no"] or 0) != int(receipt["floor_no"])
            ):
                item["status"] = "source_changed"
            elif (
                floor["content_kind"] != "discussion"
                or not repo.has_discussion_forum(int(floor["forum_id"] or 0))
            ):
                item["status"] = "source_unavailable"
            else:
                item["source_url"] = f"/threads/{int(receipt['tid'])}#pid-{int(receipt['pid'])}"
            items.append(item)
        return AgentResult(ok=True, data={"run_id": run_id, "items": items, "count": len(items), "total": total, "limit": limit, "offset": offset})
    except Exception:
        return _error("RECEIPT_QUERY_FAILED", "The Run source receipts could not be read.", retryable=True)
    finally:
        conn.close()


def _chat_record(conn, kind: str, ident: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT data FROM chat_{kind} WHERE id = ?", (ident,)).fetchone()
    return json.loads(row["data"]) if row else None


def _enabled_discussion_forums(repo: AssistantEvidenceRepository) -> list[int]:
    enabled = "TRUE" if getattr(repo.conn, "backend", None) in {"postgres", "postgresql"} else "1"
    return [int(row["forum_id"]) for row in repo.conn.execute(
        f"SELECT forum_id FROM forums WHERE content_kind = ? AND enabled = {enabled} ORDER BY forum_id",
        ("discussion",),
    ).fetchall()]


def _positive_ids(values: Sequence[int] | None) -> list[int]:
    result: list[int] = []
    for value in values or ():
        if type(value) is not int or value <= 0:
            raise ValueError("IDs must be positive integers")
        if value not in result:
            result.append(value)
    return result


def _positive_id(value: Any) -> bool:
    return type(value) is int and value > 0


def _source_content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _as_utc_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_paragraphs(
    paragraphs: list[str], *, first: int, max_bytes: int
) -> tuple[str, int, int | None, bool]:
    chunks: list[str] = []
    used = 0
    complete_end = first - 1
    partial_paragraph = None
    for offset, paragraph in enumerate(paragraphs):
        index = first + offset
        separator = "\n" if chunks else ""
        separator_bytes = len(separator.encode("utf-8"))
        encoded = paragraph.encode("utf-8")
        remaining = max_bytes - used - separator_bytes
        if len(encoded) <= remaining:
            chunks.append(paragraph)
            used += separator_bytes + len(encoded)
            complete_end = index
            continue
        # Include a safe UTF-8 prefix only when this very paragraph cannot fit;
        # its index is recorded separately and is excluded from paragraph_end.
        if complete_end < first and remaining > 0:
            prefix = encoded[:remaining].decode("utf-8", errors="ignore")
            if prefix:
                chunks.append(prefix)
                partial_paragraph = index
        return "\n".join(chunks), complete_end, partial_paragraph, True
    return "\n".join(chunks), complete_end, None, False


def _error(code: str, message: str, *, retryable: bool = False) -> AgentResult:
    return AgentResult(
        ok=False,
        error=AgentError(code=code, message=message, agent_hint=message, retryable=retryable),
    )
