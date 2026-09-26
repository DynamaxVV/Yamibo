"""隔离验收讨论检索、MCP 原文、回答引用、审批及真实 ZIP 导出。

uv run python scripts/verify_discussion_workflow.py [--serve] [--real-model]
只连接 localhost:5432 的本地开发 PostgreSQL 管理库，随机创建/最终删除临时库。
默认 FunctionModel 仅替代模型决策；其 MCP、数据库、HTTP API、导出 handler 均为真实实现。
--serve 在验收后运行隔离 Web 服务，Ctrl-C 后清理；不运行通用 daemon。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import create_engine

from yamibo_mcp.application.assistant_operation_execution import advance_operation_plans
from yamibo_mcp.config import load_settings
from yamibo_mcp.daemon.handlers.export_thread import handle_export_thread
from yamibo_mcp.db.connection import _normalize_postgres_url, connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.services.embedded_chat.runtime import EmbeddedChatService
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.web_fastapi.app import create_app

TID, PID = 990001, 99000101
TITLE = "合成验收：动画制作讨论"
CONTENT = "这是隔离验收的合成帖子，不是真实论坛观点。\n\n发言者希望动画制作有充足工期，并期待角色互动。"


def require(value, message):
    if not value:
        raise AssertionError(message)


def envelope(value):
    if isinstance(value, str):
        try:
            return envelope(json.loads(value))
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        if "plan_id" in value and "status" in value:
            return {"ok": True, "data": value}
        if isinstance(value.get("ok"), bool):
            return value
        for item in value.values():
            found = envelope(item)
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = envelope(item)
            if found is not None:
                return found
    return None


async def deterministic(messages, info):
    current = max((i for i, message in enumerate(messages) if any(isinstance(p, UserPromptPart) for p in message.parts)), default=0)
    returns = [part for message in messages[current:] for part in message.parts if isinstance(part, ToolReturnPart)]
    results = [(part.tool_name, envelope(part.content)) for part in returns]
    for name, result in results:
        if not result or not result["ok"]:
            print(f"MCP {name} failed: {result}", flush=True)
        require(result and result["ok"], f"MCP {name} failed: {result}")
    source = next((result["data"] for name, result in results if name == "read_discussion_source"), None)
    calls = [
        ("find_discussions", {"query": "动画", "tids": [TID]}),
        ("read_discussion_source", {"tid": TID, "pid": PID}),
        ("validate_discussion_citations", {"receipt_ids": [source["receipt_id"]] if source else []}),
        ("propose_operation_plan", {"action": "export", "tids": [TID], "require_images": True, "strategy": "cache_only"}),
    ]
    if len(returns) < len(calls):
        name, args = calls[len(returns)]
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"verify-{len(returns)}")}
    else:
        require(CONTENT == source["content"], "Model must actually receive fixture source")
        yield f"这份合成资料中的发言者希望制作有充足工期，并期待角色互动。[来源:{source['receipt_id']}]\n导出草案已生成，等待批准。"


def seed(settings):
    title = TitleSnapshot(TITLE, TITLE, None, None, TITLE, TITLE, "verify-discussion", [], None, None, None, None, None, [], 1.0, False)
    floor = FloorSnapshot(PID, TID, 1, "验收夹具", CONTENT, "2026-09-25T08:00:00+00:00", False)
    snapshot = ThreadSnapshot(TID, f"https://example.invalid/thread-{TID}", "discussion", TITLE, TITLE, title, "验收夹具", None, floor.pub_time, 0, [floor])
    paths = StoragePaths(settings.data_dir)
    paths.thread_dir(TID).mkdir(parents=True)
    paths.thread_context(TID).write_text(f"# {TITLE}\n\n{CONTENT}\n")
    paths.thread_metadata(TID).write_text(json.dumps(asdict(snapshot), ensure_ascii=False))
    with connect(settings) as conn:
        ThreadsRepository(conn).upsert_snapshot(snapshot, forum_id=5, context_path=str(paths.thread_context(TID)))


def request(client, method, path, **kwargs):
    response = getattr(client, method)(path, **kwargs)
    require(response.is_success, f"HTTP {method} {path}: {response.status_code} {response.text[:500]}")
    return response.json()


def exercise(settings, service, app, report):
    # No TestClient lifespan: app's original service has already been replaced.
    with TestClient(app) as client:
        found = request(client, "post", "/api/knowledge/discussions/search", json={"query": "", "tids": [TID], "forum_ids": [5]})
        require(str(TID) in json.dumps(found), "TID search must find seeded thread")
        session = request(client, "post", "/api/chat/sessions", json={"title": "隔离验收：讨论 → 导出"})
        sid = session["id"]
        run = request(client, "post", f"/api/chat/sessions/{sid}/runs", json={
            "input": f"只使用本地合成夹具：先 find_discussions 检索 TID {TID}，再 read_discussion_source 读取 PID {PID}，validate_discussion_citations 验证引用。用 [来源:receipt_id] 简要分析，再 propose_operation_plan 提出 cache_only 导出 TID {TID}（require_images=true）。不要访问远端，不要批准计划。",
            "client_request_id": uuid.uuid4().hex, "mode": "selected", "forum_ids": [5], "tids": [TID],
        })
        rid = run["run_id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = service.get_run(rid)
            if state["status"] in {"completed", "failed", "cancelled", "timed_out"}:
                break
            time.sleep(.2)
        require(state["status"] == "completed", f"Agent failed: {state}")
        messages = request(client, "get", f"/api/chat/sessions/{sid}/messages")
        assistant = [m for m in messages["messages"] if m["role"] == "assistant"][-1]
        require(assistant.get("citations"), "Answer must persist server-validated citations")
        require(assistant["citations"][0]["tid"] == TID, "Citation must reference fixture")
        source = request(client, "get", f"/api/threads/{TID}")
        require("充足工期" in json.dumps(source, ensure_ascii=False), "Local source API must expose original source")
        plans = request(client, "get", "/api/assistant/operation-plans", params={"session_id": sid})["plans"]
        require(len(plans) == 1, "Model must create exactly one export plan")
        plan = plans[0]
        with connect(settings, bootstrap=False) as conn:
            require(conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0, "No jobs before approval")
        request(client, "post", f"/api/assistant/operation-plans/{plan['plan_id']}/approve", json={"session_id": sid, "plan_version": plan["plan_version"], "plan_hash": plan["plan_hash"]})
        with connect(settings, bootstrap=False) as conn:
            advance_operation_plans(conn, settings)
        with connect(settings, bootstrap=False) as conn:
            jobs = list(conn.execute("SELECT job_id, job_type FROM jobs"))
            require(len(jobs) == 1 and jobs[0]["job_type"] == "export_thread", "Only local export is permitted")
            repo = JobsRepository(conn)
            job = repo.acquire(jobs[0]["job_id"], "workflow-verification", 120)
            require(job.payload["strategy"] == "cache_only", "Export must remain local")
            handle_export_thread(repo, job, "workflow-verification", 120, settings)
            require(repo.get(job.job_id).status == "succeeded", "Real export handler must succeed")
        with connect(settings, bootstrap=False) as conn:
            advance_operation_plans(conn, settings)
        recovered = request(client, "get", f"/api/assistant/operation-plans/{plan['plan_id']}", params={"session_id": sid})
        require(recovered["status"] == "completed", f"Plan must finish: {recovered}")
        request(client, "get", f"/api/chat/sessions/{sid}")
        exports = request(client, "get", "/api/exports")
        exported = next(item for item in exports if item["tid"] == TID)
        archive = Path(exported["export_path"])
        if not archive.is_absolute():
            archive = settings.data_dir / archive
        archive.resolve().relative_to(settings.data_dir.resolve())
        content = archive.read_bytes()
        with zipfile.ZipFile(io.BytesIO(content)) as zipped:
            require(zipped.testzip() is None, "ZIP CRC verification must pass")
            require(CONTENT in zipped.read(f"{TID}/context.md").decode(), "ZIP must contain original source")
        report.update(passed=True, session_id=sid, run_id=rid, plan_id=plan["plan_id"], answer=assistant, plan=recovered,
                      export_sha256=hashlib.sha256(content).hexdigest(), export_bytes=len(content),
                      download_gap="Existing exports API exposes filesystem paths only; ZIP verified from that exact local path.")
    return sid


def serve_worker(settings, stop):
    """Only this random database; never run a general forum/network worker."""
    while not stop.wait(.5):
        try:
            with connect(settings, bootstrap=False) as conn:
                advance_operation_plans(conn, settings)
            with connect(settings, bootstrap=False) as conn:
                rows = list(conn.execute("SELECT job_id FROM jobs WHERE status='queued' AND job_type='export_thread' AND tid=?", (TID,)))
                repo = JobsRepository(conn)
                for row in rows:
                    candidate = repo.get(row["job_id"])
                    require(candidate.payload.get("strategy") == "cache_only", "Serve worker refuses network export")
                    job = repo.acquire(candidate.job_id, "workflow-browser", 120)
                    if job:
                        handle_export_thread(repo, job, "workflow-browser", 120, settings)
        except Exception as exc:
            # Fixed synthetic IDs and no credentials in error output.
            print(f"Isolated worker failure: {type(exc).__name__}", flush=True)
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    original = load_settings() if args.real_model else None
    output = args.output or Path(tempfile.mkdtemp(prefix="yamibo-workflow-report-"))
    output.mkdir(parents=True, exist_ok=True)
    name = "yamibo_workflow_verify_" + uuid.uuid4().hex[:12]
    admin = create_engine(_normalize_postgres_url("postgresql://yamibo:yamibo@localhost:5432/postgres"), isolation_level="AUTOCOMMIT")
    created = False
    service = None
    worker = None
    stop = threading.Event()
    old_env = dict(os.environ)
    report = {"model": "configured real model" if args.real_model else "deterministic FunctionModel", "fixture": "synthetic, one discussion, no images", "passed": False}
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        with tempfile.TemporaryDirectory(prefix="yamibo-workflow-data-") as temp:
            data = Path(temp).resolve()
            config = data / "isolated.json"
            config.write_text("{}")
            for key in list(os.environ):
                if key.startswith("YAMIBO_"):
                    del os.environ[key]
            os.environ.update(YAMIBO_CONFIG_PATH=str(config), YAMIBO_DATA_DIR=str(data),
                              YAMIBO_DB_BACKEND="postgres", YAMIBO_DB_URL=f"postgresql://yamibo:yamibo@localhost:5432/{name}",
                              YAMIBO_COOKIE_FILE=str(data / "absent.cookie"), YAMIBO_RAG_ENABLED="false",
                              YAMIBO_EXPORT_DIR=str(data / "exports"), YAMIBO_NOVEL_TXT_EXPORT_DIR=str(data / "novel_exports"),
                              YAMIBO_CHAT_BACKEND="embedded", YAMIBO_LLM_BASE_URL="http://127.0.0.1:1/v1", YAMIBO_LLM_API_KEY="isolated-only")
            if original:
                os.environ.update(YAMIBO_LLM_BASE_URL=original.llm_base_url, YAMIBO_LLM_API_KEY=original.llm_api_key or "", YAMIBO_LLM_MODEL=original.llm_model)
            settings = load_settings()
            for scoped_path in (settings.data_dir, settings.export_dir, settings.novel_txt_export_dir, settings.cookie_file):
                scoped_path.resolve().relative_to(data)
            seed(settings)
            app = create_app(settings)
            app.state.chat_service.shutdown()
            service = EmbeddedChatService(settings, model=None if args.real_model else FunctionModel(stream_function=deterministic))
            app.state.chat_service = service
            sid = exercise(settings, service, app, report)
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"PASS report={output / 'report.json'} session={sid}", flush=True)
            if args.serve:
                # TestClient shutdown closed the service; restart against same isolated store.
                service = EmbeddedChatService(settings, model=None if args.real_model else FunctionModel(stream_function=deterministic))
                app.state.chat_service = service
                print(f"Isolated browser URL: http://127.0.0.1:{args.port}/rag", flush=True)
                worker = threading.Thread(target=serve_worker, args=(settings, stop), daemon=True)
                worker.start()
                import uvicorn
                uvicorn.run(app, host="127.0.0.1", port=args.port)
    finally:
        stop.set()
        if worker:
            worker.join(timeout=10)
        if service:
            service.shutdown()
        os.environ.clear()
        os.environ.update(old_env)
        if created:
            with admin.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            report["temporary_database_removed"] = True
        admin.dispose()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Report: {output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
