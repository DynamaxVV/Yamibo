from __future__ import annotations

import hashlib
import re
import time

from .store import Store, uid


def plan_key(tool, args):
    import json

    return hashlib.sha256(
        json.dumps([tool, args], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def memory_request(text):
    return bool(re.match(r"^\s*(?:请|请你|帮我)?记住(?:[：:,，。\s]|$)", text))


def explicit_request(text, tool, args):
    """Only unambiguous complete commands auto-authorize; uncertain prose goes to UI."""
    if tool == "update_agent_guidance":
        # Natural prose can request a proposal, but only an exact replacement is automatic.
        return text == "将 AGENTS.md 完整替换为：\n" + args["content"]
    return False


class Policy:
    def __init__(self, settings, run_id):
        self.settings, self.run_id = settings, run_id
        self.store = Store(settings)

    def request(self, tool, args):
        if tool in {"authorize_job_plan", "create_jobs"}:
            raise ValueError("LEGACY_JOB_TOOL_DISABLED_USE_PROPOSE_OPERATION_PLAN")
        key = plan_key(tool, args)
        with self.store.transaction() as conn:
            run = self.store.guard(self.run_id, conn)
            if tool == "update_agent_guidance" and (
                memory_request(run["input"]) or run["input"].strip() == "确认记录"
            ):
                raise ValueError("MEMORY_USE_PROPOSAL_FLOW")
            # Retries retrieve the same receipt, including unknown/denied state.
            for op in self.store.list("operations", self.run_id, conn):
                if op["plan_hash"] == key:
                    return op
            operation = dict(
                id=uid(),
                parent_id=self.run_id,
                tool=tool,
                args=args,
                plan_hash=key,
                status="pending",
                created_at=time.time(),
            )
            auto = explicit_request(run["input"], tool, args)
            if tool == "confirm_agent_memory":
                session = self.store.get("sessions", run["parent_id"], conn, lock=True)
                pending = session.get("pending_agent_memory") or {}
                prior = self.store.get("runs", pending["run_id"], conn) if pending.get("run_id") else {}
                auto = (
                    run["input"].strip() == "确认记录"
                    and pending.get("id") == args.get("proposal_id")
                    and prior.get("status") == "completed"
                    and pending.get("run_id") != self.run_id
                    and not any(
                        other["id"] not in {self.run_id, pending.get("run_id")}
                        and other.get("created_at", 0) > prior.get("created_at", 0)
                        for other in self.store.list("runs", run["parent_id"], conn)
                    )
                    and any(
                        message.get("role") == "assistant"
                        and message.get("run_id") == pending.get("run_id")
                        and pending.get("content", "") in message.get("content", "")
                        for message in session.get("messages", [])
                    )
                )
                if not auto:
                    raise ValueError("MEMORY_CONFIRMATION_REQUIRED")
            if tool in {"create_work_file", "update_work_file", "delete_work_file"}:
                # All sessions share the dedicated workspace; file safety is
                # enforced by WorkFiles, independently of file provenance.
                auto = True
            operation["status"] = "approved" if auto else "pending"
            self.store.save("operations", operation, conn)
            if not auto:
                run["status"] = "waiting_for_approval"
                self.store.save("runs", run, conn)
                self.store.event(
                    self.run_id,
                    "approval.request",
                    dict(
                        choices=["once", "deny"],
                        approval_id=operation["id"],
                        plan_hash=key,
                        tool=tool,
                        summary=args,
                    ),
                    conn,
                )
            return operation

    def approve(self, approval_id, plan_hash, choice):
        if choice not in {"once", "deny"}:
            raise ValueError("ONLY_ONCE_OR_DENY_ALLOWED")
        with self.store.transaction() as conn:
            run = self.store.guard(self.run_id, conn)
            op = self.store.get("operations", approval_id, conn, lock=True)
            if (
                op["parent_id"] != self.run_id
                or op["plan_hash"] != plan_hash
                or op["status"] != "pending"
            ):
                raise ValueError("APPROVAL_CONFLICT")
            if op["tool"] in {"authorize_job_plan", "create_jobs"}:
                # Historical pending receipts can survive a deploy. Never let
                # the old approval endpoint turn one into a direct Job grant.
                op["status"] = "denied"
                self.store.save("operations", op, conn)
                run["status"] = "running"
                self.store.save("runs", run, conn)
                self.store.event(
                    self.run_id,
                    "approval.responded",
                    dict(
                        choice="deny", resolved=True,
                        code="LEGACY_JOB_TOOL_DISABLED",
                    ),
                    conn,
                )
                return
            op["status"] = "approved" if choice == "once" else "denied"
            self.store.save("operations", op, conn)
            run["status"] = "running"
            self.store.save("runs", run, conn)
            self.store.event(
                self.run_id,
                "approval.responded",
                dict(choice=choice, resolved=True),
                conn,
            )

    def record_call(self, *, file=False):
        with self.store.transaction() as conn:
            run = self.store.guard(self.run_id, conn)
            field = "file_calls" if file else "tool_calls"
            run[field] = run.get(field, 0) + 1
            self.store.save("runs", run, conn)
