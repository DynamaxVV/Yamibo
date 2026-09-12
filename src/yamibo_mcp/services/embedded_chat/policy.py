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


def explicit_request(text, tool, args, limit):
    """Only unambiguous complete commands auto-authorize; uncertain prose goes to UI."""
    if tool == "create_jobs":
        verbs = {
            "archive": r"(?:归档|archive)",
            "update": r"(?:更新|update)",
            "export": r"(?:导出|export)",
        }
        match = re.fullmatch(
            r"\s*(?:请|帮我)?\s*"
            + verbs[args["action"]]
            + r"\s*(?:帖子)?\s*([0-9,，、\s]+)[。.!！]?\s*",
            text,
            re.I,
        )
        if match:
            tids = {int(x) for x in re.findall(r"\d+", match[1])}
            return len(tids) <= limit and set(args["tids"]) <= tids
    if tool == "update_agent_guidance":
        # Natural prose can request a proposal, but only an exact replacement is automatic.
        return text == "将 AGENTS.md 完整替换为：\n" + args["content"]
    if tool == "delete_work_file":
        return text.strip() == "删除文件 " + args["file_id"]
    return False


class Policy:
    def __init__(self, settings, run_id):
        self.settings, self.run_id = settings, run_id
        self.store = Store(settings)

    def request(self, tool, args):
        key = plan_key(tool, args)
        with self.store.transaction() as conn:
            run = self.store.guard(self.run_id, conn)
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
            auto = explicit_request(
                run["input"], tool, args, self.settings.chat_batch_limit
            )
            if tool in {"create_work_file", "update_work_file"}:
                auto = True
                if tool == "update_work_file":
                    f = self.store.get("files", args["file_id"], conn)
                    # Other-session files always require a named, visible confirmation.
                    auto = f["parent_id"] == run["parent_id"]
            if tool == "create_jobs":
                used = set(run.get("authorized_tids", []))
                merged = used | set(args["tids"])
                granted = any(
                    g["action"] == args["action"]
                    and set(args["tids"]) <= set(g["tids"])
                    for g in run.get("job_grants", [])
                )
                auto = granted or (
                    auto and len(merged) <= self.settings.chat_batch_limit
                )
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
            op["status"] = "approved" if choice == "once" else "denied"
            self.store.save("operations", op, conn)
            if choice == "once" and op["tool"] == "authorize_job_plan":
                run.setdefault("job_grants", []).append(
                    dict(op["args"], approval_id=op["id"])
                )
            run["status"] = "running"
            self.store.save("runs", run, conn)
            self.store.event(
                self.run_id,
                "approval.responded",
                dict(choice=choice, resolved=True),
                conn,
            )

    def budget(self, *, file=False):
        with self.store.transaction() as conn:
            run = self.store.guard(self.run_id, conn)
            field = "file_calls" if file else "tool_calls"
            if run.get(field, 0) >= self.settings.chat_max_tools:
                raise ValueError("TOOL_LIMIT_REACHED")
            run[field] = run.get(field, 0) + 1
            self.store.save("runs", run, conn)
