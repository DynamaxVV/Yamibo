from __future__ import annotations

import json
from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.assistant_operation_plans import (
    AssistantOperationPlansRepository,
    canonical_hash,
    new_plan_id,
)


class OperationPlanError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


_ACTIONS = {"archive", "export"}
_EXPORT_STRATEGIES = {"cache_only", "sync_if_stale", "force_resync"}
_MAX_TIDS = 25


def create_operation_plan(
    *, session_id: str, run_id: str, request_key: str, action: str,
    tids: list[int], require_images: bool,
    export_strategy: str | None = None,
) -> dict[str, Any]:
    """Create an immutable draft; no Job is created until a later executor phase."""
    if not session_id or not run_id:
        raise OperationPlanError("INVALID_OWNER_CONTEXT", "session_id and run_id are required.")
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        raise OperationPlanError("INVALID_REQUEST_KEY", "request_key must contain 1 to 128 characters.")
    request_key = request_key.strip()
    if action not in _ACTIONS:
        raise OperationPlanError("INVALID_ACTION", "action must be archive or export.")
    if not isinstance(tids, list) or not tids or len(tids) > _MAX_TIDS:
        raise OperationPlanError("INVALID_TIDS", f"Provide 1 to {_MAX_TIDS} explicit TIDs.")
    if any(type(tid) is not int or tid <= 0 for tid in tids) or len(set(tids)) != len(tids):
        raise OperationPlanError("INVALID_TIDS", "TIDs must be unique positive integers.")
    if type(require_images) is not bool:
        raise OperationPlanError("INVALID_IMAGE_REQUIREMENT", "require_images must be a boolean.")
    if action == "export" and not require_images:
        raise OperationPlanError("EXPORT_REQUIRES_IMAGES", "Discussion exports require local images.")
    if export_strategy is not None and export_strategy not in _EXPORT_STRATEGIES:
        raise OperationPlanError("INVALID_EXPORT_STRATEGY", "Unsupported export strategy.")
    if action != "export" and export_strategy is not None:
        raise OperationPlanError("INVALID_EXPORT_STRATEGY", "export_strategy only applies to export plans.")

    normalized_tids = sorted(tids)
    request = {
        "session_id": session_id, "run_id": run_id, "action": action,
        "tids": normalized_tids, "require_images": require_images,
        "export_strategy": export_strategy,
    }
    fingerprint = canonical_hash(request)
    settings = load_settings()
    if action == "export" and (export_strategy or settings.export_default_strategy) not in _EXPORT_STRATEGIES:
        raise OperationPlanError("INVALID_EXPORT_STRATEGY", "Configured export strategy is unsupported.", 409)
    with connect(settings.db_path) as conn:
        repo = AssistantOperationPlansRepository(conn)
        session_row = conn.execute(
            "SELECT data FROM chat_sessions WHERE id=?", (session_id,)
        ).fetchone()
        run_row = conn.execute(
            "SELECT parent_id, data FROM chat_runs WHERE id=?", (run_id,)
        ).fetchone()
        if session_row is None or run_row is None or str(run_row["parent_id"]) != session_id:
            raise OperationPlanError("RUN_SESSION_MISMATCH", "The Run does not belong to this session.", 404)
        session = json.loads(session_row["data"])
        run = json.loads(run_row["data"])
        owner_id = str(session.get("owner_id") or "local")
        if session.get("deleted"):
            raise OperationPlanError("SESSION_NOT_FOUND", "The session is unavailable.", 404)
        scope_id = run.get("discussion_scope_id")
        scope_row = conn.execute(
            "SELECT * FROM chat_discussion_scopes WHERE scope_id=? AND run_id=?",
            (scope_id, run_id),
        ).fetchone() if scope_id else None
        if scope_row is None:
            raise OperationPlanError("SCOPE_NOT_FOUND", "The Run has no frozen discussion scope.", 409)
        scope = dict(scope_row.items())
        if (scope["owner_id"], scope["session_id"]) != (owner_id, session_id):
            raise OperationPlanError("SCOPE_ACCESS_DENIED", "The frozen scope owner does not match.", 403)
        if scope["mode"] != "selected":
            raise OperationPlanError(
                "SCOPE_REQUIRES_SELECTION",
                "Choose exact TIDs and start a selected-scope Run before proposing an operation plan.",
                409,
            )

        existing = repo.get_by_request(owner_id, session_id, request_key)
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise OperationPlanError("IDEMPOTENCY_CONFLICT", "request_key was already used for a different plan.", 409)
            return _public_plan(existing)

        scope_tids = json.loads(scope["tids"])
        scope_forums = json.loads(scope["forum_ids"])
        scope_pids = json.loads(scope["pids"])
        if not scope_tids or set(normalized_tids) - set(scope_tids):
            raise OperationPlanError("TID_OUTSIDE_SCOPE", "Every TID must be inside the Run's frozen selected scope.", 403)
        if scope_pids:
            raise OperationPlanError("PID_SCOPE_UNSUPPORTED", "Operation plans require whole-thread selection.", 422)

        rows = conn.execute(
            f"""SELECT t.tid, t.forum_id, t.content_kind, t.archive_status, t.capture_mode,
                       t.raw_title, t.display_title, t.pub_time, f.content_kind AS forum_content_kind
                FROM threads t JOIN forums f ON f.forum_id=t.forum_id
                WHERE t.tid IN ({', '.join('?' for _ in normalized_tids)})""",
            tuple(normalized_tids),
        ).fetchall()
        by_tid = {int(row["tid"]): dict(row.items()) for row in rows}
        missing = [tid for tid in normalized_tids if tid not in by_tid]
        if missing:
            raise OperationPlanError("THREAD_NOT_FOUND", f"TID not found locally: {missing}.", 404)

        items = []
        for ordinal, tid in enumerate(normalized_tids):
            thread = by_tid[tid]
            forum_id = int(thread["forum_id"])
            if thread["content_kind"] != "discussion" or thread["forum_content_kind"] != "discussion":
                raise OperationPlanError("NOT_A_DISCUSSION", f"TID {tid} is not classified as a discussion.", 422)
            if scope_forums and forum_id not in scope_forums:
                raise OperationPlanError("TID_OUTSIDE_SCOPE", f"TID {tid} is outside the Run's frozen forum scope.", 403)
            if action == "export" and (thread["content_kind"] == "novel" or forum_id == 55):
                raise OperationPlanError("EXPORT_FORMAT_UNSUPPORTED", "This discussion export path produces ZIP files only.", 422)

            capture_mode = str(thread["capture_mode"] or "full")
            archive_status = str(thread["archive_status"] or "")
            steps = []
            if action == "archive":
                wanted_mode = "full" if require_images else "text_only"
                already_satisfied = (
                    archive_status == "complete" and (wanted_mode == "text_only" or capture_mode == "full")
                ) or (
                    wanted_mode == "text_only" and archive_status in {"complete", "partial"}
                    and capture_mode == "full"
                )
                steps.append({
                    "kind": "archive", "mode": wanted_mode,
                    "state": "satisfied" if already_satisfied else "required",
                })
            else:
                # export_thread rejects text_only archives. Make the prerequisite visible.
                if archive_status != "complete" or capture_mode != "full":
                    steps.append({
                        "kind": "archive", "mode": "full",
                        "upgrade_from": capture_mode if capture_mode == "text_only" else None,
                        "state": "required",
                    })
                steps.append({
                    "kind": "export", "strategy": export_strategy or settings.export_default_strategy,
                    "format": "zip", "requires_images": True, "state": "required",
                })
            items.append({
                "tid": tid, "ordinal": ordinal, "title": thread["display_title"] or thread["raw_title"],
                "forum_id": forum_id, "content_kind": thread["content_kind"],
                "archive_status_snapshot": archive_status, "capture_mode_snapshot": capture_mode,
                "requires_images": require_images if action == "archive" else True,
                "export_format": "zip" if action == "export" else None,
                "steps": steps, "stage": "awaiting_approval",
            })

        snapshot = {
            **request, "owner_id": owner_id, "scope_id": scope_id,
            "items": items,
        }
        plan = {
            "plan_id": new_plan_id(), "owner_id": owner_id, "session_id": session_id,
            "run_id": run_id, "request_key": request_key, "request_fingerprint": fingerprint,
            "action": action, "status": "awaiting_approval", "plan_version": 1,
            "plan_hash": canonical_hash(snapshot), "snapshot": snapshot, "items": items,
        }
        inserted = repo.insert(plan)
        if not inserted:
            # ON CONFLICT waits for a concurrent writer without aborting the transaction.
            existing = repo.get_by_request(owner_id, session_id, request_key)
            if existing and existing["request_fingerprint"] == fingerprint:
                return _public_plan(existing)
            raise OperationPlanError("IDEMPOTENCY_CONFLICT", "request_key was already used for a different plan.", 409)
        return _public_plan(repo.get(plan["plan_id"]))


def approve_operation_plan(
    *, plan_id: str, session_id: str, plan_version: int, plan_hash: str,
) -> dict[str, Any]:
    if type(plan_version) is not int or plan_version < 1 or not isinstance(plan_hash, str):
        raise OperationPlanError("INVALID_APPROVAL", "plan_version and plan_hash are required.")
    settings = load_settings()
    with connect(settings.db_path) as conn:
        repo = AssistantOperationPlansRepository(conn)
        plan = repo.get(plan_id, lock=True)
        if plan is None:
            raise OperationPlanError("PLAN_NOT_FOUND", "Operation plan not found.", 404)
        session_row = conn.execute(
            "SELECT data FROM chat_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if session_row is None:
            raise OperationPlanError("SESSION_NOT_FOUND", "Session not found.", 404)
        session = json.loads(session_row["data"])
        if session.get("deleted"):
            raise OperationPlanError("SESSION_NOT_FOUND", "Session is unavailable.", 404)
        owner_id = str(session.get("owner_id") or "local")
        if (plan["session_id"], plan["owner_id"]) != (session_id, owner_id):
            raise OperationPlanError("PLAN_ACCESS_DENIED", "Plan does not belong to this session owner.", 403)
        snapshot = plan["snapshot"]
        if (
            canonical_hash(snapshot) != plan["plan_hash"]
            or snapshot.get("items") != plan["items"]
            or snapshot.get("owner_id") != plan["owner_id"]
            or snapshot.get("session_id") != plan["session_id"]
            or snapshot.get("run_id") != plan["run_id"]
        ):
            raise OperationPlanError("PLAN_INTEGRITY_FAILURE", "Stored plan steps no longer match the frozen hash.", 409)
        if plan["status"] == "approved_pending_execution":
            if (plan["approved_version"], plan["approved_hash"]) == (plan_version, plan_hash):
                return _public_plan(plan)
            raise OperationPlanError("PLAN_ALREADY_APPROVED", "Plan was approved with a different snapshot.", 409)
        if plan["status"] != "awaiting_approval":
            raise OperationPlanError("PLAN_NOT_APPROVABLE", "Plan is no longer awaiting approval.", 409)
        if (plan["plan_version"], plan["plan_hash"]) != (plan_version, plan_hash):
            raise OperationPlanError("PLAN_SNAPSHOT_MISMATCH", "Plan version or hash does not match the frozen snapshot.", 409)
        approved = repo.approve(plan_id, expected_version=plan_version, expected_hash=plan_hash)
        if approved is None or approved["status"] != "approved_pending_execution":
            raise OperationPlanError("PLAN_APPROVAL_CONFLICT", "Plan approval changed concurrently.", 409)
        # No Job is created in this batch. The persistent state is an explicit handoff.
        return _public_plan(approved)


def get_operation_plan(*, plan_id: str, session_id: str) -> dict[str, Any]:
    settings = load_settings()
    with connect(settings.db_path, bootstrap=False) as conn:
        repo = AssistantOperationPlansRepository(conn)
        plan = repo.get(plan_id)
        if plan is None:
            raise OperationPlanError("PLAN_NOT_FOUND", "Operation plan not found.", 404)
        session_row = conn.execute("SELECT data FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if session_row is None:
            raise OperationPlanError("SESSION_NOT_FOUND", "Session not found.", 404)
        owner_id = str(json.loads(session_row["data"]).get("owner_id") or "local")
        if (plan["session_id"], plan["owner_id"]) != (session_id, owner_id):
            raise OperationPlanError("PLAN_ACCESS_DENIED", "Plan does not belong to this session owner.", 403)
        return _public_plan(plan)


def list_operation_plans(*, session_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    if not isinstance(session_id, str) or not session_id:
        raise OperationPlanError("INVALID_OWNER_CONTEXT", "session_id is required.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise OperationPlanError("INVALID_LIMIT", "limit must be between 1 and 100.")
    if type(offset) is not int or not 0 <= offset <= 10_000:
        raise OperationPlanError("INVALID_OFFSET", "offset must be between 0 and 10000.")
    settings = load_settings()
    with connect(settings.db_path, bootstrap=False) as conn:
        row = conn.execute("SELECT data FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise OperationPlanError("SESSION_NOT_FOUND", "Session not found.", 404)
        session = json.loads(row["data"])
        if session.get("deleted"):
            raise OperationPlanError("SESSION_NOT_FOUND", "Session is unavailable.", 404)
        owner_id = str(session.get("owner_id") or "local")
        repo = AssistantOperationPlansRepository(conn)
        page = repo.list_for_session(owner_id, session_id, limit=limit + 1, offset=offset)
        has_more = len(page) > limit
        plans = [_public_plan(plan) for plan in page[:limit]]
        return {"plans": plans, "limit": limit, "offset": offset, "has_more": has_more}


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    execution = "not_started" if plan["status"] in {"awaiting_approval", "approved_pending_execution"} else plan["status"]
    snapshot = plan["snapshot"]
    snapshot_valid = (
        canonical_hash(snapshot) == plan["plan_hash"]
        and snapshot.get("owner_id") == plan["owner_id"]
        and snapshot.get("session_id") == plan["session_id"]
        and snapshot.get("run_id") == plan["run_id"]
    )
    return {
        "plan_id": plan["plan_id"], "session_id": plan["session_id"],
        "run_id": plan["run_id"], "action": plan["action"], "status": plan["status"],
        "plan_version": plan["plan_version"], "plan_hash": plan["plan_hash"],
        "approved_at": plan.get("approved_at"),
        "approval_ready": plan["status"] == "awaiting_approval" and snapshot_valid,
        "items": plan["items"], "execution": execution,
    }
