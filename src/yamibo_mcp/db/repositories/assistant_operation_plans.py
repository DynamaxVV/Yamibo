from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AssistantOperationPlansRepository:
    """Persist immutable operation snapshots and their per-thread steps."""

    def __init__(self, conn):
        self.conn = conn

    def get_by_request(self, owner_id: str, session_id: str, request_key: str):
        row = self.conn.execute(
            """SELECT * FROM assistant_operation_plans
               WHERE owner_id=? AND session_id=? AND request_key=?""",
            (owner_id, session_id, request_key),
        ).fetchone()
        return self._plan(row) if row else None

    def get(self, plan_id: str, *, lock: bool = False):
        suffix = " FOR UPDATE" if lock and getattr(self.conn, "backend", "sqlite") == "postgres" else ""
        row = self.conn.execute(
            f"SELECT * FROM assistant_operation_plans WHERE plan_id=?{suffix}", (plan_id,)
        ).fetchone()
        return self._plan(row) if row else None

    def list_for_session(self, owner_id: str, session_id: str, *, limit: int, offset: int):
        rows = self.conn.execute(
            """SELECT plan_id FROM assistant_operation_plans
               WHERE owner_id=? AND session_id=?
               ORDER BY created_at DESC, plan_id DESC
               LIMIT ? OFFSET ?""",
            (owner_id, session_id, limit, offset),
        ).fetchall()
        return [self.get(str(row["plan_id"])) for row in rows]

    def insert(self, plan: dict[str, Any]) -> bool:
        now = time.time()
        inserted_result = self.conn.execute(
            """INSERT INTO assistant_operation_plans
               (plan_id, owner_id, session_id, run_id, request_key, request_fingerprint,
                action, status, plan_version, plan_hash, plan_json, approved_at,
                approved_version, approved_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
               ON CONFLICT(owner_id, session_id, request_key) DO NOTHING""",
            (
                plan["plan_id"], plan["owner_id"], plan["session_id"], plan.get("run_id"),
                plan["request_key"], plan["request_fingerprint"], plan["action"],
                plan["status"], plan["plan_version"], plan["plan_hash"],
                _json(plan["snapshot"]), now, now,
            ),
        )
        inserted = inserted_result.rowcount != 0
        if not inserted:
            return False
        for item in plan["items"]:
            self.conn.execute(
                """INSERT INTO assistant_operation_items
                   (plan_id, tid, ordinal, stage, item_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan["plan_id"], item["tid"], item["ordinal"], item["stage"],
                    _json(item), now, now,
                ),
            )
        return True

    def approve(self, plan_id: str, *, expected_version: int, expected_hash: str) -> dict[str, Any] | None:
        plan = self.get(plan_id, lock=True)
        if plan is None:
            return None
        if plan["status"] == "approved_pending_execution":
            return plan
        if plan["status"] != "awaiting_approval":
            return plan
        if plan["plan_version"] != expected_version or plan["plan_hash"] != expected_hash:
            return plan
        now = time.time()
        self.conn.execute(
            """UPDATE assistant_operation_plans
               SET status='approved_pending_execution', approved_at=?,
                   approved_version=?, approved_hash=?, updated_at=?
               WHERE plan_id=? AND status='awaiting_approval'
                 AND plan_version=? AND plan_hash=?""",
            (now, expected_version, expected_hash, now, plan_id, expected_version, expected_hash),
        )
        return self.get(plan_id)

    def list_items(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT item_json FROM assistant_operation_items WHERE plan_id=? ORDER BY ordinal",
            (plan_id,),
        ).fetchall()
        return [json.loads(row["item_json"]) for row in rows]

    def list_executable(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT plan_id FROM assistant_operation_plans
               WHERE status IN ('approved_pending_execution', 'active', 'cancellation_requested')
               ORDER BY created_at, plan_id"""
        ).fetchall()
        return [self.get(str(row["plan_id"])) for row in rows]

    def save_item(self, plan_id: str, item: dict[str, Any]) -> None:
        self.conn.execute(
            """UPDATE assistant_operation_items SET stage=?, item_json=?, updated_at=?
               WHERE plan_id=? AND tid=?""",
            (item.get("stage", "pending"), _json(item), time.time(), plan_id, item["tid"]),
        )

    def get_phase(self, plan_id: str, tid: int, step_index: int):
        row = self.conn.execute(
            """SELECT * FROM assistant_operation_phases
               WHERE plan_id=? AND tid=? AND step_index=?""",
            (plan_id, tid, step_index),
        ).fetchone()
        return dict(row.items()) if row else None

    def put_phase(
        self, *, phase_key: str, plan_id: str, tid: int, step_index: int,
        state: str, job_id: str | None = None, error_code: str | None = None,
        error_message: str | None = None, export_path: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO assistant_operation_phases
               (phase_key, plan_id, tid, step_index, state, job_id, error_code,
                error_message, export_path, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(plan_id, tid, step_index) DO UPDATE SET
                 state=excluded.state, job_id=excluded.job_id,
                 error_code=excluded.error_code, error_message=excluded.error_message,
                 export_path=excluded.export_path, updated_at=excluded.updated_at""",
            (phase_key, plan_id, tid, step_index, state, job_id,
             error_code, error_message, export_path, time.time()),
        )

    def set_status(self, plan_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE assistant_operation_plans SET status=?, updated_at=? WHERE plan_id=?",
            (status, time.time(), plan_id),
        )

    def request_cancel(self, plan_id: str) -> dict[str, Any] | None:
        plan = self.get(plan_id, lock=True)
        if plan is None:
            return None
        if plan["status"] in {"completed", "partially_completed", "failed", "cancelled"}:
            return plan
        self.set_status(plan_id, "cancellation_requested")
        return self.get(plan_id)

    def _plan(self, row) -> dict[str, Any]:
        plan = dict(row.items())
        plan["snapshot"] = json.loads(plan.pop("plan_json"))
        plan["items"] = self.list_items(plan["plan_id"])
        return plan


def canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def new_plan_id() -> str:
    return uuid.uuid4().hex
