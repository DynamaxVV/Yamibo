"""本地知识库流程演练，不修改已有数据库或源存档。

运行：uv run python scripts/demo_knowledge_pipeline.py
需要本地 PostgreSQL 和 data/threads 中的示例。自动创建并删除隔离数据库。
仅模拟 LLM 和 embedding；MCP、Agent 运行时、索引 handler、PG 均为真实实现。
这是现状验证，不是增量索引实现；活动和覆盖 SQL 也仅是演练探针。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from yamibo_mcp.config import load_settings
from yamibo_mcp.daemon.handlers.rag_index import handle_rag_index
from yamibo_mcp.db.connection import _normalize_postgres_url, connect
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.services.embedded_chat.runtime import EmbeddedChatService


SAMPLE_TIDS = (553545, 36025, 572373)
MARKER = "流程演练新增回复标记"
NEW_PID = 990000001


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def iso_time(value):
    """示例中无时区的论坛时间按 UTC+8 解读；不回写源文件。"""
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        nums = [int(x) for x in re.findall(r"\d+", value)]
        date = datetime(*(nums + [0] * (6 - len(nums))))
    return date.replace(tzinfo=timezone(timedelta(hours=8))).isoformat() if date.tzinfo is None else date.isoformat()


def load_sample(root, tid):
    path = root / str(tid) / "metadata.json"
    raw = path.read_bytes()
    data = json.loads(raw)
    title = TitleSnapshot(**{f.name: data["title"][f.name] for f in fields(TitleSnapshot)})
    source_floors = sorted(data["floors"], key=lambda f: (datetime.fromisoformat(iso_time(f["pub_time"])), f["pid"]))
    floors = [FloorSnapshot(
        pid=f["pid"], tid=tid, floor_no=i, publisher=f.get("publisher"),
        publisher_uid=f.get("publisher_uid"), content=f.get("content") or "",
        pub_time=iso_time(f.get("pub_time")), has_images=f.get("has_images", False),
        image_urls=f.get("image_urls", []), quote_text=f.get("quote_text"), reply_text=f.get("reply_text"),
    ) for i, f in enumerate(source_floors, 1)]
    snapshot = ThreadSnapshot(
        tid=tid, url=data.get("url"), page_type=data["page_type"], raw_title=data["raw_title"],
        display_title=data["display_title"], title=title, publisher=data.get("publisher"),
        publisher_uid=data.get("publisher_uid"), pub_time=iso_time(data.get("pub_time")),
        permission=data.get("permission", 0), floors=floors, image_count=data.get("image_count", 0),
    )
    return snapshot, data["context_inputs"]["forum_id"], {
        "tid": tid, "sha256": hashlib.sha256(raw).hexdigest(), "posts": len(floors),
        "demo_floor_mapping": [{"pid": f["pid"], "source_floor": f["floor_no"], "demo_floor": i} for i, f in enumerate(source_floors, 1)],
    }


class FakeEmbeddings(BaseHTTPRequestHandler):
    inputs = 0
    requests = 0
    fail = False

    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        texts = payload["input"]
        texts = [texts] if isinstance(texts, str) else texts
        type(self).inputs += len(texts)
        type(self).requests += 1
        if self.fail:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"error":"simulated embedding failure"}')
            return
        output = []
        for i, value in enumerate(texts):
            vector = [0.0] * 512
            for j in range(max(1, len(value) - 1)):
                index = int.from_bytes(hashlib.sha256(value[j:j + 2].encode()).digest()[:4], "big") % 512
                vector[index] += 1
            norm = math.sqrt(sum(x*x for x in vector)) or 1
            output.append({"index": i, "embedding": [x/norm for x in vector]})
        body = json.dumps({"data": output}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def envelope(value):
    """兼容 MCP 文本内容和 Pydantic AI 的结构化工具返回。"""
    if isinstance(value, str):
        try:
            return envelope(json.loads(value))
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
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


async def call(client, name, args):
    result = await client.call_tool(name, args)
    data = envelope(result.model_dump(mode="json"))
    require(not result.isError and data is not None and data["ok"], f"MCP {name}: {data}")
    return data["data"]


def measure(settings):
    with connect(settings, bootstrap=False) as conn:
        counts = dict(conn.execute("""SELECT
          (SELECT COUNT(*) FROM threads) threads,
          (SELECT COUNT(*) FROM floors) posts,
          COUNT(*) chunks, COUNT(DISTINCT pid) FILTER (WHERE embedding_status='indexed') indexed_posts
          FROM rag_chunks""").fetchone())
        counts["jobs"] = [dict(r) for r in conn.execute("SELECT status, COUNT(*) count FROM jobs GROUP BY status")]
        counts["per_thread"] = [dict(r) for r in conn.execute("""SELECT t.tid, t.raw_title,
          (SELECT COUNT(*) FROM floors f WHERE f.tid=t.tid) posts,
          (SELECT COUNT(DISTINCT pid) FROM rag_chunks c WHERE c.tid=t.tid AND c.embedding_status='indexed') indexed_posts
          FROM threads t ORDER BY t.tid""")]
        return counts


async def index(client, settings, tid):
    before = FakeEmbeddings.inputs
    receipt = await call(client, "create_rag_index_job", {"tid": tid})
    # 仅执行刚刚创建的指定任务，不运行 acquire_next，不消费其它队列。
    with connect(settings, bootstrap=False) as conn:
        repo = JobsRepository(conn)
        job = repo.acquire(receipt["job_id"], "demo", 120)
        handle_rag_index(repo, job, "demo", 120, settings)
    status = await call(client, "read_job", {"job_id": receipt["job_id"]})
    return {"status": status, "embedding_inputs": FakeEmbeddings.inputs-before}


async def agent_summary(settings):
    calls = [
        ("probe_archived_threads", {"tids": [553545]}),
        ("search_archived_content", {"query": "动画", "mode": "keyword", "top_k": 5}),
        ("read_archived_thread", {"tid": 553545, "view": "content", "chunk_size": 50}),
    ]
    observed = []

    async def fake_model(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if returns:
            result = envelope(returns[-1].content)
            require(result is not None and result["ok"], "模拟模型必须收到成功的真实工具结果")
            observed.append({"tool": returns[-1].tool_name, "result": result})
        if len(returns) < len(calls):
            name, args = calls[len(returns)]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"demo-{len(returns)}")}
        else:
            data = envelope(returns[-1].content)["data"]
            evidence = [f for f in data["floors"] if any(w in f["content"] for w in ("制作", "经费", "崩"))]
            require(evidence, "必须从实际原文中找到示例证据")
            lines = ["模拟模型生成：当前读取的《动画化情报》存在对制作的担忧或期待。仅描述该帖，不推断全板块趋势。"]
            for f in evidence[:3]:
                lines.append(f"- PID {f['pid']}：{f['content'][:100]}")
            yield "\n".join(lines)

    service = EmbeddedChatService(settings, model=FunctionModel(stream_function=fake_model), autostart=False)
    try:
        session = service.create_session(title="知识库演练（模拟模型）")
        run = service.start_run(session["id"], "只读取本地示例，检索动画讨论，读取 553545 并附原文总结。", "demo-1")
        service.store.update("runs", run["run_id"], status="running", deadline=time.time()+120)
        await asyncio.wait_for(service.model_run(run["run_id"]), 120)
        service.finish(run["run_id"], "completed")
        events = service.store.events(run["run_id"], 0)
        require(len([e for e in events if e["type"] == "tool.completed"]) == 3, "必须真实执行三个 MCP 工具")
        return {"messages": service.get_messages(session["id"]), "tool_results": observed, "events": events}
    finally:
        service.shutdown()


async def exercise(settings, samples, report):
    with connect(settings, bootstrap=False) as conn:
        for snapshot, forum, _ in samples:
            ThreadsRepository(conn).upsert_snapshot(snapshot, forum_id=forum)
    report["imported"] = measure(settings)
    with tempfile.TemporaryFile(mode="w+") as err:
        params = StdioServerParameters(command=sys.executable, args=["-m", "yamibo_mcp.server.cli", "stdio"], env=os.environ.copy())
        async with stdio_client(params, errlog=err) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                report["public_tool_count"] = len((await client.list_tools()).tools)
                report["initial_jobs"] = [await index(client, settings, s.tid) for s, _, _ in samples]
                require(all(j["status"]["status"] == "succeeded" for j in report["initial_jobs"]), "初次索引必须成功")
                report["indexed"] = measure(settings)
                report["searches"] = {mode: await call(client, "search_archived_content", {"query": "动画", "mode": mode, "top_k": 5}) for mode in ("keyword", "vector", "hybrid")}
                require(all(r["count"] for r in report["searches"].values()), "三种检索都必须返回数据")
                report["agent"] = await agent_summary(settings)
                snapshot, forum, _ = samples[0]
                reply = FloorSnapshot(pid=NEW_PID, tid=snapshot.tid, floor_no=len(snapshot.floors)+1, publisher="模拟用户", publisher_uid="demo", pub_time="2026-09-12T10:00:00+08:00", has_images=False, content=f"{MARKER}：这是人为添加的测试回复，希望新的动画制作有充分的工期和经费。")
                changed = replace(snapshot, floors=[*snapshot.floors, reply])
                with connect(settings, bootstrap=False) as conn:
                    ThreadsRepository(conn).upsert_snapshot(changed, forum_id=forum)
                    # 仅探针，不冒充已经存在的公共 MCP 活动接口。
                    report["activity_sql_probe"] = [dict(r) for r in conn.execute("SELECT tid,pid,pub_time,content FROM floors WHERE pub_time >= ? AND pub_time < ? ORDER BY pub_time,pid", ("2026-09-12T00:00:00+08:00", "2026-09-13T00:00:00+08:00"))]
                require([r["pid"] for r in report["activity_sql_probe"]] == [NEW_PID], "旧帖新回复按发言时间进入当日活动")
                stale = await call(client, "search_archived_content", {"query": MARKER, "mode": "keyword", "top_k": 5})
                require(stale["count"] == 0, "记录现状：归档更新没有立即更新索引")
                report["before_reindex"] = stale
                report["update_job"] = await index(client, settings, snapshot.tid)
                fresh = await call(client, "search_archived_content", {"query": MARKER, "mode": "keyword", "top_k": 5})
                require(any(r["pid"] == NEW_PID for r in fresh["items"]), "重建后必须通过 MCP 检索到新 PID")
                report["after_reindex"] = fresh
                counts = measure(settings)
                # 再导入相同快照，验证导入本身也不会重复计数。
                with connect(settings, bootstrap=False) as conn:
                    ThreadsRepository(conn).upsert_snapshot(changed, forum_id=forum)
                report["unchanged_repeat_job"] = await index(client, settings, snapshot.tid)
                repeat_counts = measure(settings)
                require(counts["posts"] == repeat_counts["posts"] and counts["chunks"] == repeat_counts["chunks"], "重复索引不能增加重复分块")
                report["after_repeat"] = repeat_counts
                FakeEmbeddings.fail = True
                try:
                    report["embedding_failure_job"] = await index(client, settings, snapshot.tid)
                finally:
                    FakeEmbeddings.fail = False
                require(report["embedding_failure_job"]["status"]["status"] == "partial", "模拟故障必须在任务状态中可见")
                report["keyword_during_failure"] = await call(client, "search_archived_content", {"query": MARKER, "mode": "keyword", "top_k": 5})
                require(report["keyword_during_failure"]["count"] > 0, "embedding 失败后文本检索仍须可用")
                report["recovery_job"] = await index(client, settings, snapshot.tid)
                require(report["recovery_job"]["status"]["status"] == "succeeded", "恢复后索引必须成功")
                report["final_counts"] = measure(settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root", type=Path, default=Path(__file__).resolve().parents[1]/"data"/"threads")
    parser.add_argument("--output", type=Path, help="报告目录；默认在系统临时目录创建并保留")
    args = parser.parse_args()
    original = load_settings()
    url = make_url(original.db_url or "")
    require(original.db_backend == "postgres" and url.host in {"127.0.0.1", "localhost", "::1"}, "仅允许使用本地 PostgreSQL 创建演练库")
    samples = [load_sample(args.sample_root.resolve(), tid) for tid in SAMPLE_TIDS]
    output = (args.output or Path(tempfile.mkdtemp(prefix="yamibo-knowledge-report-"))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"samples": [r for _, _, r in samples], "mocked": ["LLM: scripted FunctionModel", "embedding: local deterministic 512-dimensional hash vectors"], "limitations": ["验证链路，不评价语义检索或模型理解质量", "导入仅在演练库按时间排序并重新编号楼层，保留 PID 与源映射；未复制图片", "活动与覆盖是 SQL 探针，未新增正式 MCP 接口", "更新需要显式重建整帖索引；不是自动增量处理"]}
    name = "yamibo_knowledge_demo_" + uuid.uuid4().hex[:12]
    admin = create_engine(_normalize_postgres_url(original.db_url), isolation_level="AUTOCOMMIT")
    created = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEmbeddings)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        with tempfile.TemporaryDirectory(prefix="yamibo-knowledge-data-") as temp:
            data = Path(temp).resolve()
            config = data/"demo.json"
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            config.write_text(json.dumps({"rag": {"enabled": True, "base_url": endpoint, "api_key": "demo-only", "embedding_model": "text-embedding-3-demo", "embedding_dimensions": 512}}))
            # 不继承本地实际服务地址或密钥；子 MCP 也读取同一演练配置。
            for key in list(os.environ):
                if key.startswith("YAMIBO_"):
                    del os.environ[key]
            os.environ.update(YAMIBO_CONFIG_PATH=str(config), YAMIBO_DATA_DIR=str(data), YAMIBO_DB_BACKEND="postgres", YAMIBO_DB_URL=url.set(database=name).render_as_string(hide_password=False), YAMIBO_CHAT_BACKEND="embedded", YAMIBO_LLM_API_KEY="demo-only", YAMIBO_LLM_BASE_URL=endpoint, YAMIBO_RAG_ENABLED="true", YAMIBO_RAG_BASE_URL=endpoint, YAMIBO_RAG_API_KEY="demo-only", YAMIBO_RAG_EMBEDDING_MODEL="text-embedding-3-demo", YAMIBO_RAG_EMBEDDING_DIMENSIONS="512")
            settings = load_settings()
            connect(settings).close()
            asyncio.run(asyncio.wait_for(exercise(settings, samples, report), 240))
            for info in report["samples"]:
                require(hashlib.sha256((args.sample_root/str(info["tid"])/"metadata.json").read_bytes()).hexdigest() == info["sha256"], "源示例不得修改")
            report["passed"] = True
    finally:
        server.shutdown()
        server.server_close()
        if created:
            with admin.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            report["temporary_database_removed"] = True
        admin.dispose()
        (output/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(f"演练报告：{output / 'report.json'}", flush=True)
    summary = ["# 知识库本地流程演练", "", "仅模拟 LLM 与 embedding，使用真实 PostgreSQL、公共 MCP、内置 Agent 和索引 handler。", "", f"导入：{report['imported']['threads']} 篇 / {report['imported']['posts']} 条发言。", f"首次索引：{report['indexed']['chunks']} 个分块。", "", "## 模拟 Agent 的输出", "", report["agent"]["messages"]["messages"][-1]["content"], "", "## 更新验证", "", "新增测试回复能够按发言时间查到；显式重建索引后可通过 MCP 检索到。", f"新增一条后 embedding 输入数：{report['update_job']['embedding_inputs']}；不改内容再次索引输入数：{report['unchanged_repeat_job']['embedding_inputs']}。", "这证明现有路径仍是整帖重建，尚未实现向量复用与自动增量更新。", "", "已验证 embedding 故障后的文本检索和恢复重建；所有临时数据已清理。", "", "## 边界", "", *[f"- {x}" for x in report["limitations"]]]
    (output/"summary.md").write_text("\n".join(summary)+"\n")
    print(f"摘要：{output / 'summary.md'}", flush=True)


if __name__ == "__main__":
    main()
