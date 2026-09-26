from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from typing import get_type_hints

# Reused by the lazily imported tool_server module when constructing each Run.
from mcp.server.fastmcp import FastMCP
from pydantic import ConfigDict, create_model

from yamibo_mcp.server import agent_tools
from yamibo_mcp.server.agent_adapter import to_wire
from yamibo_mcp.domain.forums import default_forums

from .files import WorkFiles
from .read_boundary import job_status, daily_issue_status
from .policy import Policy, memory_request
from .store import encode
from .store import uid

# Explicit argument lists are intentionally narrower than the public registry.
READS = {
    "read_job": ("job_id",),
}


# Public capability names are authoritative; deployment/credential overrides are not model input.
UNSAFE_PUBLIC_ARGUMENTS = {"base_url", "cookie_file", "html_path", "url"}
PUBLIC_TOOLS = {name: (description, handler) for name, description, handler in agent_tools.PUBLIC_AGENT_TOOLS}


def requested_forum_ids(text: str) -> set[int]:
    """Resolve explicit forum names and IDs from one user turn, without guessing."""
    names = {profile.forum_id for profile in default_forums() if profile.name in text}
    numeric = {
        int(match)
        for match in re.findall(r"(?:forum_id|fid)\s*[:：=]?\s*(\d+)", text, re.I)
    }
    return names | numeric


def public_input_model(name):
    handler = PUBLIC_TOOLS[name][1]
    hints = get_type_hints(handler)
    return create_model(
        name + "PublicInput", __config__=ConfigDict(extra="forbid"),
        **{key: (hints[key], parameter.default if parameter.default is not inspect.Parameter.empty else ...)
           for key, parameter in inspect.signature(handler).parameters.items()
           if key not in UNSAFE_PUBLIC_ARGUMENTS},
    )


def public_description(name):
    description, handler = PUBLIC_TOOLS[name]
    metadata = getattr(handler, "__capability_metadata__", {})
    return description + "\nFor this task, use list_project_skills/read_project_skill; read_yamibo_guidance provides public MCP reference. Capability metadata: " + json.dumps(metadata, ensure_ascii=False) + (
        "\nWrites require an exact user approval. Creating a Job is not completion; read_job verifies its result."
        if metadata.get("effect") not in {"read_only", "remote_read"} else
        "\nFrozen Run scope is enforced before reads. Unsupported evidence readers return SCOPED_READER_REQUIRED; use find_discussions/read_discussion_source. For named forum scope first use read_forum_profiles. Remote search reads one page per call; local results cannot substitute for remote results."
    )


def validate_public_arguments(name: str, args: dict) -> dict:
    """Bound dynamic MCP inputs before a handler can touch a database or forum."""
    integer_limits = {
        "forum_id": 100_000, "tid": 2_147_483_647, "series_id": 2_147_483_647,
        "page": 100, "start_page": 100, "end_page": 100, "top_k": 100,
        "floor_start": 2_147_483_647, "floor_end": 2_147_483_647,
        "chunk_size": 16_000, "embedding_dimensions": 4_096,
        "retention_success_runs": 100, "min_floor_count": 1_000_000,
        "min_thread_count": 1_000_000, "min_user_count": 1_000_000,
        "expected_revision": 2_147_483_647,
    }
    for key, maximum in integer_limits.items():
        value = args.get(key)
        if value is not None and (type(value) is not int or not 1 <= value <= maximum):
            raise ValueError("PUBLIC_ARGUMENT_OUT_OF_RANGE:" + key)
    since = args.get("since_event_id")
    if since is not None and (type(since) is not int or not 0 <= since <= 2_147_483_647):
        raise ValueError("PUBLIC_ARGUMENT_OUT_OF_RANGE:since_event_id")
    tids = args.get("tids")
    if tids is not None and (
        not isinstance(tids, list) or not 1 <= len(tids) <= 200
        or any(type(tid) is not int or not 1 <= tid <= 2_147_483_647 for tid in tids)
    ):
        raise ValueError("INVALID_TIDS")
    for key, value in args.items():
        if isinstance(value, str) and len(value) > (500 if key == "query" else 2_000):
            raise ValueError("PUBLIC_ARGUMENT_TOO_LARGE:" + key)
        if isinstance(value, (dict, list)) and len(encode(value).encode("utf-8")) > 8_192:
            raise ValueError("PUBLIC_ARGUMENT_TOO_LARGE:" + key)
    if name == "create_archive_schedule" and len(args["name"]) > 120:
        raise ValueError("PUBLIC_ARGUMENT_TOO_LARGE:name")
    if args.get("floor_start") and args.get("floor_end") and args["floor_start"] > args["floor_end"]:
        raise ValueError("INVALID_FLOOR_RANGE")
    confidence = args.get("min_confidence")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("PUBLIC_ARGUMENT_OUT_OF_RANGE:min_confidence")
    return args


def clean(value, settings, maximum=65536):
    """Bound output and redact configured secrets before persistence or model use."""
    from pydantic_core import to_jsonable_python
    text = encode(to_jsonable_python(value))
    for secret in (
        settings.llm_api_key,
        settings.hermes_api_key,
        settings.db_url,
        settings.chat_access_token,
    ):
        if secret:
            text = text.replace(secret, "[redacted]")
    # Do not disclose filesystem layout embedded in tool metadata/errors.
    text = text.replace(str(settings.data_dir), "[data]")
    if maximum is not None and len(text.encode()) > maximum:
        return {
            "ok": False,
            "error": {
                "code": "RESULT_TOO_LARGE",
                "message": "请缩小查询范围或按页读取",
            },
        }
    return json.loads(text)


class RestrictedTools:
    def __init__(self, settings, run_id):
        self.settings = settings
        self.policy = Policy(settings, run_id)
        self.store = self.policy.store
        self.files = WorkFiles(settings, self.store)

    def require_scoped_public_read(self, name, args):
        """Reject before I/O when a reader cannot express the frozen scope.

        New public read tools are denied by default. Source evidence goes through
        scoped discovery/source tools so PID/time filters and receipts apply.
        """
        scope, _ = self.discussion_scope()
        if name in {"read_forum_profiles", "read_job", "wait_for_job"}:
            return
        if name in {"search_forum_threads", "browse_forum_page"} and (
            scope.get("mode") == "discovery"
            and not any(scope.get(key) for key in ("tids", "pids", "start_at", "end_at", "report_revision"))
        ):
            forum_id = args.get("forum_id")
            remote_forums = set(scope.get("remote_forum_ids") or ())
            if forum_id not in scope["forum_ids"] and forum_id not in remote_forums:
                # The frozen evidence scope intentionally contains discussion
                # boards only. Explicit remote targets are frozen separately
                # from the Run's immutable user request and do not widen local
                # source reads.
                raise ValueError("FORUM_SCOPE_MISMATCH")
            if forum_id in remote_forums and not self.forum_is_enabled(forum_id):
                raise ValueError("FORUM_SCOPE_MISMATCH")
            return
        raise ValueError("SCOPED_READER_REQUIRED")

    def forum_is_enabled(self, forum_id):
        from yamibo_mcp.db.connection import connect
        from yamibo_mcp.db.repositories.forums import ForumsRepository

        conn = connect(self.settings, bootstrap=False)
        try:
            forum = ForumsRepository(conn).get_forum(forum_id)
            return forum is not None and bool(forum["enabled"])
        finally:
            conn.close()

    def reads(self, name, args):
        if name not in READS or set(args) - set(READS[name]):
            raise ValueError("TOOL_OR_ARGUMENT_NOT_ALLOWED")
        for key, value in args.items():
            if (
                value is not None
                and key
                in {
                    "tid",
                    "forum_id",
                    "page",
                    "start_page",
                    "top_k",
                    "floor_start",
                    "floor_end",
                    "chunk_size",
                }
                and (not isinstance(value, int) or isinstance(value, bool) or value < 1)
            ):
                raise ValueError("POSITIVE_INTEGER_REQUIRED")
            if (
                key in {"page", "start_page", "top_k", "chunk_size"}
                and value is not None
                and value > 100
            ):
                raise ValueError("QUERY_LIMIT_EXCEEDED")
            if isinstance(value, str) and len(value) > 2000:
                raise ValueError("QUERY_TOO_LARGE")
            if key == "tids" and (
                not value
                or len(value) > 200
                or any(type(t) is not int or t < 1 for t in value)
            ):
                raise ValueError("INVALID_TIDS")
        self.require_scoped_public_read(name, args)
        return clean(job_status(self.settings, args["job_id"]), self.settings)

    async def public_call(self, name, args):
        args = public_input_model(name).model_validate(args).model_dump()
        if name == "search_forum_threads":
            # A date-wide search ignores the page bounds in the remote query layer.
            # Require the model to traverse one explicit page at a time.
            if args.get("posted_on") is not None:
                raise ValueError("POSTED_ON_UNBOUNDED")
            args["end_page"] = args["start_page"]
        args = validate_public_arguments(name, args)
        if "forum_id" in args:
            scope, _ = self.discussion_scope()
            explicit_forums = set(scope.get("remote_forum_ids") or ())
            if explicit_forums and args.get("forum_id") not in explicit_forums:
                raise ValueError("FORUM_SCOPE_MISMATCH")
        for key in ("page", "start_page"):
            if key in args and (type(args[key]) is not int or args[key] < 1):
                raise ValueError("POSITIVE_INTEGER_REQUIRED")
        if name == "wait_for_job":
            args["timeout_seconds"] = min(max(args["timeout_seconds"], 0), 120)
            args["poll_interval_seconds"] = min(max(args["poll_interval_seconds"], 1), 10)
        handler = PUBLIC_TOOLS[name][1]
        effect = getattr(handler, "__capability_metadata__", {}).get("effect")
        with self.store.transaction() as conn:
            self.store.guard(self.policy.run_id, conn)
        if effect in {"read_only", "remote_read"}:
            self.require_scoped_public_read(name, args)
            if name in {"read_job", "wait_for_job"}:
                # Public artifacts/events may contain report bodies. Query only
                # status columns, never those unscoped evidence blobs.
                if name == "wait_for_job":
                    return await self.wait_for_job_status(args)
                return clean(await asyncio.to_thread(job_status, self.settings, args["job_id"]), self.settings)
            return clean(await asyncio.to_thread(handler, **args), self.settings)
        op = self.policy.request(name, args)
        while op["status"] == "pending":
            await asyncio.sleep(0.25)
            with self.store.transaction() as conn:
                self.store.guard(self.policy.run_id, conn)
                op = self.store.get("operations", op["id"], conn)
        if op["status"] == "completed":
            return op["result"]
        with self.store.transaction() as conn:
            self.store.guard(self.policy.run_id, conn)
            current = self.store.get("operations", op["id"], conn, lock=True)
            if current["status"] != "approved":
                return current.get("result", {"ok": False, "error": {
                    "code": current["status"], "message": "未获授权或结果待核实，禁止自动重试"}})
            current["status"] = "outcome_unknown"
            self.store.save("operations", current, conn)
        # Persist uncertainty before external effects. Exceptions retain this receipt,
        # preventing duplicate submissions even across process restart.
        result = clean(await asyncio.to_thread(handler, **args), self.settings)
        with self.store.transaction() as conn:
            current = self.store.get("operations", op["id"], conn, lock=True)
            current.update(status="completed", result=result)
            self.store.save("operations", current, conn)
        return result

    async def wait_for_job_status(self, args):
        deadline = asyncio.get_running_loop().time() + args["timeout_seconds"]
        while True:
            with self.store.transaction() as conn:
                self.store.guard(self.policy.run_id, conn)
            result = await asyncio.to_thread(job_status, self.settings, args["job_id"])
            if not result.get("ok") or result["data"]["is_terminal"]:
                return clean(result, self.settings)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                result["data"]["timed_out"] = True
                return clean(result, self.settings)
            await asyncio.sleep(min(args["poll_interval_seconds"], remaining))

    def read_daily_issue(self, issue_id: str):
        self.discussion_scope()
        run = self.store.get("runs", self.policy.run_id)
        session = self.store.get("sessions", run["parent_id"])
        return {"ok": True, "data": daily_issue_status(
            self.settings, issue_id=issue_id, owner_id=str(session.get("owner_id") or "local"),
        )}

    async def create_daily_issue(self, target_day: str, forum_ids: list[int]):
        from datetime import date
        from yamibo_mcp.application.daily_brief_service import create_manual_issue

        day = date.fromisoformat(target_day)
        if not forum_ids or len(forum_ids) > 20 or any(type(fid) is not int or fid <= 0 for fid in forum_ids):
            raise ValueError("INVALID_FORUM_IDS")
        args = {"target_day": day.isoformat(), "forum_ids": sorted(set(forum_ids))}
        op = self.policy.request("create_daily_issue", args)
        while op["status"] == "pending":
            await asyncio.sleep(0.25)
            with self.store.transaction() as conn:
                self.store.guard(self.policy.run_id, conn)
                op = self.store.get("operations", op["id"], conn)
        if op["status"] == "completed":
            return op["result"]
        with self.store.transaction() as conn:
            run = self.store.guard(self.policy.run_id, conn)
            current = self.store.get("operations", op["id"], conn, lock=True)
            if current["status"] != "approved":
                return {"ok": False, "error": {"code": current["status"]}}
            current["status"] = "outcome_unknown"
            self.store.save("operations", current, conn)
        result = clean({"ok": True, "data": await asyncio.to_thread(
            create_manual_issue, session_id=run["parent_id"], target_day=day,
            forum_ids=args["forum_ids"], settings=self.settings,
        )}, self.settings)
        with self.store.transaction() as conn:
            current = self.store.get("operations", op["id"], conn, lock=True)
            current.update(status="completed", result=result)
            self.store.save("operations", current, conn)
        return result

    def read_daily_report(self):
        """Read only the immutable report snapshot bound to this Run."""
        from yamibo_mcp.application.daily_brief_report_queries import read_daily_report_snapshot

        run = self.store.get("runs", self.policy.run_id)
        snapshot = run.get("daily_report_snapshot")
        scope = run.get("discussion_scope") or {}
        if (
            run.get("scope_pending")
            or not isinstance(snapshot, dict)
            or snapshot.get("revision_id") != scope.get("report_revision")
        ):
            raise ValueError("DAILY_REPORT_NOT_FROZEN")
        return clean(
            read_daily_report_snapshot(snapshot=snapshot, settings=self.settings), self.settings
        )

    def propose_agent_memory(self, content: str):
        content = content.strip()
        if not content or len(content) > 500 or "\n" in content:
            raise ValueError("INVALID_MEMORY_CONTENT")
        with self.store.transaction() as conn:
            run = self.store.guard(self.policy.run_id, conn)
            if not memory_request(run["input"]) or any(
                phrase in run["input"] for phrase in ("不要记住", "别记住", "无需记住")
            ):
                raise ValueError("MEMORY_REQUEST_REQUIRED")
            session = self.store.get("sessions", run["parent_id"], conn, lock=True)
            existing = session.get("pending_agent_memory") or {}
            if existing.get("run_id") == self.policy.run_id:
                if existing.get("content") != content:
                    raise ValueError("MEMORY_PROPOSAL_ALREADY_EXISTS")
                proposal = existing
            else:
                proposal = {"id": uid(), "content": content, "run_id": self.policy.run_id}
                session["pending_agent_memory"] = proposal
                self.store.save("sessions", session, conn)
        return {"ok": True, "data": {
            "proposal_id": proposal["id"], "content": content,
            "question": f"我理解你要我长期记住：{content}。是否记录到 AGENTS.md？请回复“确认记录”或“取消记录”。",
        }}

    def cancel_agent_memory(self):
        with self.store.transaction() as conn:
            run = self.store.guard(self.policy.run_id, conn)
            if run["input"].strip() != "取消记录":
                raise ValueError("MEMORY_CANCELLATION_REQUIRED")
            session = self.store.get("sessions", run["parent_id"], conn, lock=True)
            session.pop("pending_agent_memory", None)
            self.store.save("sessions", session, conn)
        return {"ok": True, "data": {"cancelled": True}}

    def discussion_scope(self):
        run = self.store.get("runs", self.policy.run_id)
        if run.get("scope_pending") or not run.get("discussion_scope_id"):
            raise ValueError("DISCUSSION_SCOPE_UNAVAILABLE")
        return run["discussion_scope"], run["discussion_scope_id"]

    def find_discussions(self, *, query, forum_ids, tids, start_date, end_date, limit):
        from yamibo_mcp.application.discussion_discovery_queries import find_discussions
        from yamibo_mcp.db.repositories.discussion_search import parse_date_bound

        scope, _scope_id = self.discussion_scope()
        if scope["pids"]:
            from yamibo_mcp.application.contracts import AgentError, AgentResult

            return clean(to_wire(AgentResult(
                ok=False,
                error=AgentError(
                    code="SCOPE_REQUIRES_SELECTED_FLOORS",
                    message="This Run is limited to selected PIDs; read those exact floors directly.",
                    agent_hint="Call read_discussion_source with a frozen TID and PID.",
                ),
            )), self.settings)
        forums = scope["forum_ids"]
        if not forums:
            return {"ok": True, "data": {"count": 0, "items": [], "result_status": "complete"}}
        if forum_ids is not None:
            forums = sorted(set(forums) & set(forum_ids))
            if not forums:
                return {"ok": True, "data": {"count": 0, "items": [], "result_status": "complete"}}
        scoped_tids = scope["tids"]
        if tids is not None:
            requested = sorted(set(tids))
            if scoped_tids:
                requested = sorted(set(requested) & set(scoped_tids))
            if not requested:
                return {"ok": True, "data": {"count": 0, "items": [], "result_status": "complete"}}
            scoped_tids = requested

        frozen_start = parse_date_bound(scope["start_at"])
        frozen_end = parse_date_bound(scope["end_at"])
        requested_start = parse_date_bound(start_date)
        requested_end = parse_date_bound(end_date, end=True)
        effective_start = max((x for x in (frozen_start, requested_start) if x is not None), default=None)
        effective_end = min((x for x in (frozen_end, requested_end) if x is not None), default=None)
        if effective_start is not None and effective_end is not None and effective_start >= effective_end:
            return {"ok": True, "data": {"count": 0, "items": [], "result_status": "complete"}}
        result = find_discussions(
            query=query, forum_ids=forums, tids=scoped_tids or None,
            start_date=effective_start.isoformat() if effective_start else None,
            end_date=effective_end.isoformat() if effective_end else None,
            limit=limit,
        )
        return clean(to_wire(result), self.settings)

    def read_discussion_source(self, *, tid, pid, paragraph_start, paragraph_end, max_bytes):
        from yamibo_mcp.application.assistant_evidence_queries import read_discussion_source

        _scope, scope_id = self.discussion_scope()
        result = read_discussion_source(
            scope_id=scope_id, run_id=self.policy.run_id, tid=tid, pid=pid,
            paragraph_start=paragraph_start, paragraph_end=paragraph_end,
            max_bytes=max_bytes,
        )
        return clean(to_wire(result), self.settings)

    def validate_discussion_citations(self, *, receipt_ids):
        from yamibo_mcp.application.assistant_evidence_queries import validate_source_receipts

        self.discussion_scope()
        result = validate_source_receipts(run_id=self.policy.run_id, receipt_ids=receipt_ids)
        response = to_wire(result)
        if isinstance(response.get("data"), dict):
            # The host uses excerpts to render answer cards. The model already
            # read them, so keep this validation receipt small.
            response["data"].pop("sources", None)
        return clean(response, self.settings)

    async def invoke(self, tool, args, handler, *, file=False):
        self.policy.record_call(file=file)
        self.store.event(
            self.policy.run_id,
            "tool.started",
            {"tool": tool, "preview": clean(args, self.settings)},
        )
        try:
            result = await handler()
        except Exception as exc:
            # No raw provider, DB, HTTP or filesystem exception text crosses the boundary.
            allowed = {
                "SCOPED_READER_REQUIRED",
                "DISCUSSION_SCOPE_UNAVAILABLE",
                "FILE_NOT_FOUND",
                "FILE_UNAVAILABLE",
                "FILE_REVISION_CONFLICT",
                "WORKSPACE_QUOTA_EXCEEDED",
                "RUN_STOPPED",
                "RUN_LIMIT_REACHED",
                "INVALID_FILE_NAME",
                "INVALID_OWNER_CONTEXT",
                "INVALID_REQUEST_KEY",
                "INVALID_ACTION",
                "INVALID_TIDS",
                "INVALID_IMAGE_REQUIREMENT",
                "EXPORT_REQUIRES_IMAGES",
                "INVALID_EXPORT_STRATEGY",
                "RUN_SESSION_MISMATCH",
                "SESSION_NOT_FOUND",
                "SCOPE_NOT_FOUND",
                "SCOPE_ACCESS_DENIED",
                "SCOPE_REQUIRES_SELECTION",
                "TID_OUTSIDE_SCOPE",
                "PID_SCOPE_UNSUPPORTED",
                "THREAD_NOT_FOUND",
                "NOT_A_DISCUSSION",
                "EXPORT_FORMAT_UNSUPPORTED",
                "IDEMPOTENCY_CONFLICT",
                "DAILY_REPORT_NOT_FROZEN",
                "DAILY_ISSUE_NOT_FOUND",
                "INVALID_FORUM_IDS",
                "CHAT_DAILY_REPORT_UNAVAILABLE",
                "FORUM_SCOPE_MISMATCH",
                "POSTED_ON_UNBOUNDED",
                "INVALID_MEMORY_CONTENT",
                "MEMORY_REQUEST_REQUIRED",
                "MEMORY_PROPOSAL_ALREADY_EXISTS",
                "MEMORY_CONFIRMATION_REQUIRED",
                "MEMORY_CANCELLATION_REQUIRED",
                "MEMORY_USE_PROPOSAL_FLOW",
            }
            code = getattr(exc, "code", None)
            result = {
                "ok": False,
                "error": {
                    "code": code if code in allowed else (
                        str(exc) if str(exc) in allowed else "TOOL_FAILED"
                    ),
                    "message": (
                        "此读取入口无法证明符合当前分析范围。请使用 find_discussions / read_discussion_source；日报正文请从知识库打开指定版本后使用 read_daily_report。"
                        if str(exc) == "SCOPED_READER_REQUIRED"
                        else "操作未完成，请查看输入、授权或已有任务状态"
                    ),
                },
            }
        result = clean(result, self.settings)
        self.store.event(
            self.policy.run_id, "tool.completed", {"tool": tool, "result": result}
        )
        return result

    async def write(self, tool, args):
        op = self.policy.request(tool, args)
        while op["status"] == "pending":
            await asyncio.sleep(0.25)
            with self.store.transaction() as conn:
                self.store.guard(self.policy.run_id, conn)
                op = self.store.get("operations", op["id"], conn)
        if op["status"] == "completed":
            return op["result"]
        if op["status"] != "approved":
            return {
                "ok": False,
                "error": {
                    "code": op["status"],
                    "message": "未获授权或执行结果待核实，禁止自动重试",
                },
            }
        # File-system and database commits cannot be atomic. Persist the uncertainty
        # marker BEFORE touching bytes; a crash blocks replay instead of overwriting.
        with self.store.transaction() as conn:
            self.store.guard(self.policy.run_id, conn)
            current = self.store.get("operations", op["id"], conn, lock=True)
            if current["status"] != "approved":
                return current.get(
                    "result", {"ok": False, "error": {"code": current["status"]}}
                )
            current["status"] = "outcome_unknown"
            self.store.save("operations", current, conn)
        with self.store.transaction() as conn:
            run = self.store.guard(self.policy.run_id, conn)
            self.store.get(
                "files", "guidance", conn, lock=True
            )  # global file mutation lock
            result = self.files.mutate(tool, args, run, conn, op["id"])
            if tool == "confirm_agent_memory":
                session = self.store.get("sessions", run["parent_id"], conn, lock=True)
                session.pop("pending_agent_memory", None)
                self.store.save("sessions", session, conn)
            op.update(status="completed", result={"ok": True, "data": result})
            self.store.save("operations", op, conn)
            self.store.event(self.policy.run_id, "file.changed", result, conn)
        return op["result"]

    def propose_operation_plan(
        self, *, action, tids, require_images, strategy=None
    ):
        from yamibo_mcp.application.assistant_operation_plans import create_operation_plan

        run = self.store.get("runs", self.policy.run_id)
        if run.get("scope_pending") or not run.get("discussion_scope_id"):
            raise ValueError("DISCUSSION_SCOPE_UNAVAILABLE")
        scope = run.get("discussion_scope") or {}
        if scope.get("mode") != "selected":
            from yamibo_mcp.application.assistant_operation_plans import OperationPlanError

            raise OperationPlanError(
                "SCOPE_REQUIRES_SELECTION",
                "Choose exact TIDs and start a selected-scope Run before proposing an operation plan.",
                409,
            )
        if scope.get("pids"):
            from yamibo_mcp.application.assistant_operation_plans import OperationPlanError

            raise OperationPlanError(
                "PID_SCOPE_UNSUPPORTED",
                "Operation plans require whole-thread selection.",
                422,
            )
        if not isinstance(tids, list) or not tids or any(type(tid) is not int for tid in tids):
            from yamibo_mcp.application.assistant_operation_plans import OperationPlanError

            raise OperationPlanError("INVALID_TIDS", "Provide explicit positive TIDs.", 400)
        normalized_tids = sorted(tids)
        if len(set(normalized_tids)) != len(normalized_tids):
            from yamibo_mcp.application.assistant_operation_plans import OperationPlanError

            raise OperationPlanError("INVALID_TIDS", "TIDs must be unique.", 400)
        if not set(normalized_tids) <= set(scope.get("tids") or []):
            from yamibo_mcp.application.assistant_operation_plans import OperationPlanError

            raise OperationPlanError(
                "TID_OUTSIDE_SCOPE", "Every TID must be inside this Run's selected scope.", 403
            )

        # The model cannot choose an approval key. Exact retries in this Run are
        # idempotent; changed inputs receive a distinct server-derived key.
        request = {
            "run_id": self.policy.run_id,
            "action": action,
            "tids": normalized_tids,
            "require_images": require_images,
            "strategy": strategy,
        }
        request_key = "embedded-chat:" + hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return clean(
            create_operation_plan(
                session_id=run["parent_id"], run_id=self.policy.run_id,
                request_key=request_key, action=action, tids=normalized_tids,
                require_images=require_images,
                export_strategy=strategy,
            ),
            self.settings,
        )


def build_restricted_server(settings, run_id):
    """Build the isolated per-Run MCP server."""
    from .tool_server import build_restricted_server as register_tools

    return register_tools(settings, run_id)
