"""纯文本知识库演练：真实 PG/MCP/内置 Agent，脚本模拟 LLM，不使用 embedding。

复用同目录 demo_knowledge_pipeline 的样例导入与模拟 Agent；不改变正式运行路径。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from demo_knowledge_pipeline import (
    MARKER, NEW_PID, SAMPLE_TIDS, agent_summary, call, load_sample, require,
)
from yamibo_mcp.config import load_settings
from yamibo_mcp.daemon.handlers.rag_index import _load_indexable_chunks
from yamibo_mcp.db.connection import _normalize_postgres_url, connect
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot


GUARD = '''import sys
def guard(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("demo forbids external network")
sys.addaudithook(guard)
import yamibo_mcp.rag.embeddings as embeddings
def forbidden(*args, **kwargs):
    raise AssertionError("embedding is forbidden in this demo")
embeddings.build_embedding_provider = forbidden
'''


def text_index(settings, tid):
    """执行现有清洗/分块和文本落库，明确绕过需要向量的正式索引 handler。"""
    with connect(settings, bootstrap=False) as conn:
        repo = ThreadsRepository(conn)
        chunks = _load_indexable_chunks(
            settings=settings, thread_row=repo.get_thread(tid),
            title_row=repo.get_title_parse(tid), floor_rows=repo.list_floors(tid),
        )
        rows = RagChunksRepository(conn).replace_thread_chunks(
            tid=tid, chunks=chunks, embedding_model="not-used-text-demo", embedding_dimensions=0,
        )
        # 保留仓储默认的 pending；indexed_at 是现有必填写入时间，不能代表向量完成。
        # 不新增正式系统状态，也不能把纯文本冒充向量 indexed。
        return {"tid": tid, "chunks_written": len(rows), "embedding_used": False}


def coverage(settings):
    with connect(settings, bootstrap=False) as conn:
        return dict(conn.execute("""SELECT
            (SELECT COUNT(*) FROM threads) threads,
            (SELECT COUNT(*) FROM floors) posts,
            COUNT(*) text_chunks, COUNT(DISTINCT pid) text_covered_posts,
            COUNT(*) FILTER (WHERE embedding_status='indexed') vector_indexed_chunks,
            (SELECT COUNT(*) FROM jobs) jobs
            FROM rag_chunks""").fetchone())


def activity(settings, start, end, limit=7):
    """演练 SQL：稳定分页，不伪称已提供正式公共 MCP 活动工具。"""
    items, cursor, pages = [], None, 0
    with connect(settings, bootstrap=False) as conn:
        while True:
            after, params = "", [5, start, end]
            if cursor:
                after = " AND (f.pub_time,f.pid) > (?,?)"
                params.extend(cursor)
            params.append(limit)
            rows = [dict(r) for r in conn.execute(f"""SELECT f.tid,f.pid,f.pub_time,f.content
                FROM floors f JOIN threads t ON t.tid=f.tid
                WHERE t.forum_id=? AND f.pub_time>=? AND f.pub_time<? {after}
                ORDER BY f.pub_time,f.pid LIMIT ?""", tuple(params))]
            if not rows:
                break
            pages += 1
            items.extend(rows)
            cursor = (rows[-1]["pub_time"], rows[-1]["pid"])
    require(len(items) == len({r["pid"] for r in items}), "活动分页不能重复 PID")
    return {"items": items, "pages": pages, "interface": "demo SQL probe"}


async def exercise(settings, samples, report):
    def save(snapshot, forum):
        with connect(settings, bootstrap=False) as conn:
            ThreadsRepository(conn).upsert_snapshot(snapshot, forum_id=forum)

    for snapshot, forum, _ in samples:
        save(snapshot, forum)
    report["imported"] = coverage(settings)
    report["text_index"] = [text_index(settings, s.tid) for s, _, _ in samples]
    report["initial_coverage"] = coverage(settings)
    report["historical_activity"] = activity(settings, "2025-01-01T00:00:00+08:00", "2025-04-01T00:00:00+08:00")
    require(len(report["historical_activity"]["items"]) == len(samples[0][0].floors), "历史活动分页应覆盖样例全部回复")
    with tempfile.TemporaryFile(mode="w+") as err:
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "yamibo_mcp.server.cli", "stdio"], env=os.environ.copy())
        async with stdio_client(params, errlog=err) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                report["public_tool_count"] = len((await client.list_tools()).tools)

                async def search(query):
                    return await call(client, "search_archived_content", {"query": query, "mode": "keyword", "top_k": 10})

                report["initial_search"] = await search("动画")
                require(report["initial_search"]["count"] > 0, "无向量时 MCP 关键词检索仍需成功")
                snapshot, forum, _ = samples[0]
                reply = FloorSnapshot(pid=NEW_PID, tid=snapshot.tid,
                    floor_no=len(snapshot.floors)+1, publisher="演练用户", publisher_uid="demo",
                    pub_time="2026-09-12T10:00:00+08:00", has_images=False,
                    content=f"{MARKER}：人为测试内容，希望新的动画制作有充分工期和经费。")
                changed = replace(snapshot, floors=[*snapshot.floors, reply])
                save(changed, forum)
                recent = activity(settings, "2026-09-12T00:00:00+08:00", "2026-09-13T00:00:00+08:00")
                require([r["pid"] for r in recent["items"]] == [NEW_PID], "旧帖新回复应进入近期活动，历史导入不应混入")
                report["recent_activity"] = recent
                report["before_refresh"] = await search(MARKER)
                require(report["before_refresh"]["count"] == 0, "记录当前没有自动更新文本索引的事实")
                report["append_refresh"] = text_index(settings, snapshot.tid)
                report["after_refresh"] = await search(MARKER)
                require(any(r["pid"] == NEW_PID for r in report["after_refresh"]["items"]), "手动刷新后应命中新 PID")
                revised_marker = "流程演练修改回复标记"
                edited = replace(changed, floors=[*snapshot.floors, replace(reply,
                    content=f"{revised_marker}：人为修订，讨论重点改为字幕翻译的质量。")])
                save(edited, forum)
                report["edit_refresh"] = text_index(settings, snapshot.tid)
                require((await search(MARKER))["count"] == 0, "修改后旧正文不应继续命中")
                report["edited_search"] = await search(revised_marker)
                require(any(r["pid"] == NEW_PID for r in report["edited_search"]["items"]), "修订后 PID 必须保持不变")
                before = coverage(settings)
                save(edited, forum)
                report["repeat_refresh"] = text_index(settings, snapshot.tid)
                require(coverage(settings) == before, "重复导入与重建不应重复计数")
                report["agent"] = await agent_summary(settings)
                report["final_coverage"] = coverage(settings)
                require(report["final_coverage"]["vector_indexed_chunks"] == 0, "不能产生向量索引")
                require(report["final_coverage"]["jobs"] == 0, "不能执行正式后台队列任务")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root", type=Path, default=Path(__file__).resolve().parents[1]/"data"/"threads")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    original = load_settings()
    url = make_url(original.db_url or "")
    require(original.db_backend == "postgres" and url.host in {"127.0.0.1", "localhost", "::1"}, "仅允许本地 PG")
    samples = [load_sample(args.sample_root.resolve(), tid) for tid in SAMPLE_TIDS]
    output = (args.output or Path(tempfile.mkdtemp(prefix="yamibo-text-report-"))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "embedding": "disabled, provider construction forbidden",
        "samples": [r for _, _, r in samples], "limitations": [
            "LLM 是预设工具调用脚本，不验证智能规划或总结质量",
            "活动与覆盖是 SQL 探针；Agent 仅调用现有探测、搜索和原文读取 MCP 工具",
            "演练显式调用现有清洗与分块落库，绕过正式索引 handler 的 embedding 阶段",
            "更新仍是手动整帖重建，不是自动增量索引",
            "临时库按时间重编号楼层，保留 PID、原文和映射；图片未导入",
            "文本可搜不等于向量已索引；演练不新增正式文本完成状态",
        ]}
    name = "yamibo_text_demo_" + uuid.uuid4().hex[:12]
    admin = create_engine(_normalize_postgres_url(original.db_url), isolation_level="AUTOCOMMIT")
    created = False
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        with tempfile.TemporaryDirectory(prefix="yamibo-text-data-") as temp:
            data = Path(temp).resolve()
            (data/"sitecustomize.py").write_text(GUARD)
            for key in list(os.environ):
                if key.startswith("YAMIBO_"):
                    del os.environ[key]
            os.environ.update(YAMIBO_CONFIG_PATH=str(data/"absent.json"), YAMIBO_DATA_DIR=str(data),
                YAMIBO_DB_BACKEND="postgres", YAMIBO_DB_URL=url.set(database=name).render_as_string(hide_password=False),
                YAMIBO_CHAT_BACKEND="embedded", YAMIBO_LLM_API_KEY="unused-demo-key",
                YAMIBO_LLM_BASE_URL="http://127.0.0.1:1/unused", YAMIBO_RAG_ENABLED="true",
                YAMIBO_RAG_BASE_URL="http://127.0.0.1:1/forbidden", YAMIBO_RAG_API_KEY="unused-demo-key")
            os.environ["PYTHONPATH"] = os.pathsep.join([str(data), str(Path(__file__).resolve().parents[1]/"src"), os.environ.get("PYTHONPATH", "")])
            exec(GUARD, {})
            settings = load_settings()
            connect(settings).close()
            asyncio.run(asyncio.wait_for(exercise(settings, samples, report), 120))
            for info in report["samples"]:
                require(hashlib.sha256((args.sample_root/str(info["tid"])/"metadata.json").read_bytes()).hexdigest() == info["sha256"], "源样例不得修改")
            report["passed"] = True
    finally:
        if created:
            with admin.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            report["temporary_database_removed"] = True
        admin.dispose()
        (output/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(f"报告：{output/'report.json'}", flush=True)
    summary = ["# 知识库纯文本演练", "", "通过。真实 PostgreSQL / MCP / 内置 Agent；LLM 为脚本模拟，embedding 完全禁用。", "",
        f"导入 {report['imported']['threads']} 篇 / {report['imported']['posts']} 条发言。",
        f"首次文本分块 {report['initial_coverage']['text_chunks']} 个，覆盖 {report['initial_coverage']['text_covered_posts']} 条发言。",
        "历史活动分页、旧帖新回复、修订替换、重复导入去重均通过。",
        "新增与修订必须手动刷新整帖；未实现自动增量索引。", "",
        "## 模拟 Agent 的输出", "", report['agent']['messages']['messages'][-1]['content'], "",
        "## 边界", "", *[f"- {v}" for v in report['limitations']], "",
        "临时数据库已删除，原样例哈希未变。"]
    (output/"summary.md").write_text("\n".join(summary)+"\n")
    print(f"摘要：{output/'summary.md'}", flush=True)


if __name__ == "__main__":
    main()
