from __future__ import annotations

import asyncio
import inspect
import json
from typing import Literal, get_type_hints

from mcp.server.fastmcp import FastMCP
from pydantic import ConfigDict, create_model

from yamibo_mcp.server import agent_tools
from yamibo_mcp.server.agent_adapter import to_wire

from .files import WorkFiles
from .policy import Policy
from .store import encode

# Explicit argument lists are intentionally narrower than the public registry.
READS = {
    "browse_forum_page": ("page", "forum_id", "order"),
    "search_forum_threads": ("query", "forum_id", "start_page"),
    "inspect_remote_thread": ("tid",),
    "probe_archived_threads": ("tids",),
    "read_archived_thread": (
        "tid",
        "view",
        "floor_start",
        "floor_end",
        "cursor",
        "chunk_size",
    ),
    "read_forum_profiles": (),
    "check_thread_updates": ("tid",),
    "search_archived_content": ("query", "mode", "top_k"),
    "read_job": ("job_id",),
    "read_job_events": ("job_id",),
}


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
        if name == "search_forum_threads":
            args = dict(args, end_page=args.get("start_page", 1))
        return clean(getattr(agent_tools, name)(**args), self.settings)

    async def invoke(self, tool, args, handler, *, file=False):
        self.policy.budget(file=file)
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
                "FILE_NOT_AGENT_OWNED",
                "FILE_REVISION_CONFLICT",
                "WORKSPACE_QUOTA_EXCEEDED",
                "RUN_STOPPED",
                "RUN_LIMIT_REACHED",
                "TOOL_LIMIT_REACHED",
                "INVALID_FILE_NAME",
            }
            result = {
                "ok": False,
                "error": {
                    "code": str(exc) if str(exc) in allowed else "TOOL_FAILED",
                    "message": "操作未完成，请查看输入、授权或已有任务状态",
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
        if tool == "authorize_job_plan":
            result = {"ok": True, "data": {"authorized": args, "approval_id": op["id"]}}
            self.store.update("operations", op["id"], status="completed", result=result)
            return result
        if tool == "create_jobs":
            return self.jobs(op)
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
            op.update(status="completed", result={"ok": True, "data": result})
            self.store.save("operations", op, conn)
            self.store.event(self.policy.run_id, "file.changed", result, conn)
        return op["result"]

    def jobs(self, op):
        from yamibo_mcp.application import archive_commands

        with self.store.transaction() as conn:
            run = self.store.guard(self.policy.run_id, conn)
            current = self.store.get("operations", op["id"], conn, lock=True)
            if current["status"] == "completed":
                return current["result"]
            if current["status"] != "approved":
                raise ValueError("APPROVAL_REQUIRED")
            args = op["args"]
            results = []
            functions = {
                "archive": archive_commands.create_thread_archive_job,
                "update": archive_commands.create_thread_update_job,
                "export": archive_commands.create_thread_export_job,
            }
            prior = {}
            for receipt in self.store.list("operations", self.policy.run_id, conn):
                if (
                    receipt["tool"] == "create_jobs"
                    and receipt["status"] == "completed"
                    and receipt["args"]["action"] == args["action"]
                ):
                    prior.update(
                        zip(receipt["args"]["tids"], receipt["result"]["data"]["items"])
                    )
            for tid in args["tids"]:
                if tid in prior:
                    results.append(prior[tid])
                    continue
                result = functions[args["action"]](tid=tid, connection=conn)
                results.append(to_wire(result))
            receipt = {
                "ok": True,
                "data": {
                    "items": results,
                    "job_ids": [r["data"]["job_id"] for r in results],
                },
            }
            current.update(status="completed", result=receipt)
            self.store.save("operations", current, conn)
            run["authorized_tids"] = sorted(
                set(run.get("authorized_tids", [])) | set(args["tids"])
            )
            self.store.save("runs", run, conn)
            return receipt


def build_restricted_server(settings, run_id):
    if not run_id:
        raise ValueError("embedded-chat requires a bound run")
    tools = RestrictedTools(settings, run_id)
    tools.store.get("runs", run_id)
    server = FastMCP(
        "yamibo-embedded-chat",
        instructions="只提供受限 Yamibo 业务与工作文件能力。创建 Job 不等于完成。",
    )
    for name, fields in READS.items():
        handler = getattr(agent_tools, name)
        signature = inspect.signature(handler)
        hints = get_type_hints(handler)
        definitions = {
            key: (
                hints[key],
                signature.parameters[key].default
                if signature.parameters[key].default is not inspect.Parameter.empty
                else ...,
            )
            for key in fields
        }
        model = create_model(
            name + "Input", __config__=ConfigDict(extra="forbid"), **definitions
        )

        def make(name, model):
            async def call(**kwargs):
                args = model.model_validate(kwargs).model_dump()

                async def execute():
                    return await asyncio.to_thread(tools.reads, name, args)

                return await tools.invoke(name, args, execute)

            call.__name__ = name
            call.__signature__ = inspect.Signature(
                [
                    inspect.Parameter(
                        k,
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=f.annotation,
                        default=inspect.Parameter.empty
                        if f.is_required()
                        else f.default,
                    )
                    for k, f in model.model_fields.items()
                ],
                return_annotation=dict,
            )
            return call

        server.tool(name=name, description=f"受限业务查询 {name}")(make(name, model))

    @server.tool()
    async def read_operation_history(offset: int = 0) -> dict:
        """分页读取当前会话的操作事实；用于长会话裁剪后核查 Job 和未完成操作。"""
        if offset < 0:
            raise ValueError("INVALID_OFFSET")

        async def execute():
            from .runtime import operation_facts

            run = tools.store.get("runs", run_id)
            facts = operation_facts(tools.store, run["parent_id"])
            return {
                "ok": True,
                "data": {
                    "items": facts[offset : offset + 20],
                    "next_offset": offset + 20 if len(facts) > offset + 20 else None,
                },
            }

        return await tools.invoke("read_operation_history", {"offset": offset}, execute)

    @server.tool()
    async def authorize_job_plan(
        action: Literal["archive", "update", "export"], tids: list[int]
    ) -> dict:
        """批量任务先列出完整目标并确认一次；确认后可分批 create_jobs，不重复询问。最多 1000 帖。"""
        if (
            not tids
            or len(tids) > 1000
            or any(type(t) is not int or t < 1 for t in tids)
        ):
            raise ValueError("INVALID_TIDS")
        args = {"action": action, "tids": sorted(set(tids))}
        return await tools.invoke(
            "authorize_job_plan", args, lambda: tools.write("authorize_job_plan", args)
        )

    @server.tool()
    async def create_jobs(
        action: Literal["archive", "update", "export"], tids: list[int]
    ) -> dict:
        """按明确授权创建归档/更新/导出 Job；只表示提交，等待结果用 wait_for_jobs。"""
        if (
            not tids
            or len(tids) > 200
            or any(type(t) is not int or t < 1 for t in tids)
        ):
            raise ValueError("INVALID_TIDS")
        args = {"action": action, "tids": sorted(set(tids))}
        return await tools.invoke(
            "create_jobs", args, lambda: tools.write("create_jobs", args)
        )

    @server.tool()
    async def wait_for_jobs(job_ids: list[str]) -> dict:
        """需要任务完成后继续整理时使用。程序等待，不反复调用模型。"""
        if not job_ids or len(job_ids) > 200:
            raise ValueError("INVALID_JOB_IDS")

        async def wait():
            with tools.store.transaction() as conn:
                run = tools.store.guard(run_id, conn)
                run["status"] = "waiting_jobs"
                tools.store.save("runs", run, conn)
            previous = None
            try:
                while True:
                    with tools.store.transaction() as conn:
                        tools.store.guard(run_id, conn)
                    results = [
                        await asyncio.to_thread(tools.reads, "read_job", {"job_id": j})
                        for j in job_ids
                    ]
                    status = [(r.get("data") or {}).get("status") for r in results]
                    if status != previous:
                        tools.store.event(
                            run_id,
                            "job.progress",
                            {"job_ids": job_ids, "statuses": status},
                        )
                        previous = status
                    if all(
                        not r.get("ok")
                        or (r.get("data") or {}).get("result_ready")
                        or (r.get("data") or {}).get("is_terminal")
                        or (r.get("data") or {}).get("status")
                        in {"failed", "cancelled", "completed"}
                        for r in results
                    ):
                        return {"ok": True, "data": results}
                    await asyncio.sleep(5)
            finally:
                with tools.store.transaction() as conn:
                    run = tools.store.get("runs", run_id, conn, lock=True)
                    if run["status"] == "waiting_jobs":
                        run["status"] = "running"
                        tools.store.save("runs", run, conn)

        return await tools.invoke("wait_for_jobs", {"job_ids": job_ids}, wait)

    @server.tool()
    async def list_work_files() -> dict:
        """列出专属目录文件，用户导入文件只读。"""

        async def execute():
            return {"ok": True, "data": tools.files.listing()}

        return await tools.invoke("list_work_files", {}, execute, file=True)

    @server.tool()
    async def read_work_file(file_id: str, offset: int = 0) -> dict:
        """按文件 ID 分段读取 UTF-8 文本。"""

        async def execute():
            return {"ok": True, "data": tools.files.read(file_id, offset)}

        return await tools.invoke(
            "read_work_file", {"file_id": file_id, "offset": offset}, execute, file=True
        )

    @server.tool()
    async def create_work_file(name: str, content: str) -> dict:
        """创建新的工作文本，不能覆盖已有文件。"""
        args = dict(name=name, content=content)
        return await tools.invoke(
            "create_work_file",
            args,
            lambda: tools.write("create_work_file", args),
            file=True,
        )

    @server.tool()
    async def update_work_file(
        file_id: str, expected_revision: int, content: str
    ) -> dict:
        """修改 Agent 自建文件，保留旧版本；外部改动导致冲突。"""
        args = dict(
            file_id=file_id, expected_revision=expected_revision, content=content
        )
        return await tools.invoke(
            "update_work_file",
            args,
            lambda: tools.write("update_work_file", args),
            file=True,
        )

    @server.tool()
    async def delete_work_file(file_id: str, expected_revision: int) -> dict:
        """仅在用户明确要求时移入回收区；无永久删除能力。"""
        args = dict(file_id=file_id, expected_revision=expected_revision)
        return await tools.invoke(
            "delete_work_file",
            args,
            lambda: tools.write("delete_work_file", args),
            file=True,
        )

    @server.tool()
    async def update_agent_guidance(expected_revision: int, content: str) -> dict:
        """用户明确要求时更新独立 AGENTS.md，不能改变程序权限。"""
        args = dict(expected_revision=expected_revision, content=content)
        return await tools.invoke(
            "update_agent_guidance",
            args,
            lambda: tools.write("update_agent_guidance", args),
            file=True,
        )

    @server.resource("yamibo://agent/guidance")
    def guidance() -> str:
        with tools.store.transaction() as conn:
            tools.store.guard(run_id, conn)
        return encode(
            {
                "revision": tools.store.get("files", "guidance")["revision"],
                "content": tools.files.guidance(),
            }
        )

    @server.resource("yamibo://schema/capabilities")
    async def capabilities() -> str:
        return encode(
            {
                "profile": "embedded-chat",
                "tools": [t.model_dump(mode="json") for t in await server.list_tools()],
            }
        )

    # No general URI reader, public resource templates, prompts or subscriptions.
    return server
