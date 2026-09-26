import asyncio
import json
import os
import time
import uuid
from dataclasses import replace

import pytest

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.services.embedded_chat.mcp import (
    RestrictedTools,
    build_restricted_server,
)
from yamibo_mcp.services.embedded_chat.policy import Policy
from yamibo_mcp.services.embedded_chat.runtime import EmbeddedChatService


@pytest.fixture(
    params=["sqlite", "postgres"]
    if os.environ.get("YAMIBO_AGENT_TEST_PG_URL")
    else ["sqlite"]
)
def settings(tmp_path, monkeypatch, request):
    monkeypatch.setenv("YAMIBO_DB_BACKEND", "sqlite")
    monkeypatch.delenv("YAMIBO_DB_URL", raising=False)
    monkeypatch.setenv("YAMIBO_DATA_DIR", str(tmp_path.resolve()))
    monkeypatch.setenv("YAMIBO_DB_PATH", str(tmp_path.resolve() / "test.db"))
    monkeypatch.setenv("YAMIBO_CONFIG_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setenv("YAMIBO_LLM_API_KEY", "fake-test-key")
    admin = None
    database = None
    if request.param == "postgres":
        from sqlalchemy import create_engine, make_url

        url = make_url(os.environ["YAMIBO_AGENT_TEST_PG_URL"])
        database = "agent_test_" + uuid.uuid4().hex
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.exec_driver_sql(f'CREATE DATABASE "{database}"')
        monkeypatch.setenv("YAMIBO_DB_BACKEND", "postgres")
        monkeypatch.setenv(
            "YAMIBO_DB_URL",
            url.set(database=database).render_as_string(hide_password=False),
        )
    s = replace(load_settings(), chat_backend="embedded", chat_access_token=None)
    connect(s).close()
    yield s
    if admin is not None:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture
def service(settings):
    from pydantic_ai.models.test import TestModel

    svc = EmbeddedChatService(settings, model=TestModel(call_tools=[]), autostart=False)
    yield svc
    svc.shutdown()


def test_chat_context_reads_saved_model_without_restarting_service(service, settings, monkeypatch):
    monkeypatch.delenv("YAMIBO_LLM_API_KEY", raising=False)
    settings.config_path.write_text(json.dumps({"llm": {
        "base_url": "http://localhost:8317/v1", "api_key": "new-key", "model": "new-model",
    }}))
    assert service.context()["model"] == "new-model"
    assert service.context()["ready"] is True
    assert service.settings.llm_model != "new-model"


def run(service, text="你好"):
    s = service.create_session()
    r = service.start_run(s["id"], text, "request-1")
    service.store.update(
        "runs", r["run_id"], status="running", deadline=time.time() + 20
    )
    return r


def _citation_run(service, settings):
    with connect(settings, bootstrap=False) as conn:
        conn.execute("""INSERT INTO threads(tid, page_type, raw_title, pub_time, sync_time,
            last_pid, archive_status, validation_status, forum_id, content_kind)
            VALUES(78001, 'discussion', '引用测试', '2026-09-25', '2026-09-25',
            88001, 'complete', 'valid', 5, 'discussion')""")
        conn.execute("""INSERT INTO floors(pid, tid, floor_no, content, pub_time, has_images, content_hash)
            VALUES(88001, 78001, 1, '第一条原文', '2026-09-25', 0, 'fixture')""")
    session = service.create_session()
    created = service.start_run(session["id"], "分析此帖", "citation-test", mode="selected", tids=[78001])
    service.store.update("runs", created["run_id"], status="running", deadline=time.time() + 30)
    tools = RestrictedTools(settings, created["run_id"])
    result = tools.read_discussion_source(tid=78001, pid=88001, paragraph_start=1, paragraph_end=None, max_bytes=8000)
    assert result["ok"], result
    return created, result["data"]["receipt_id"]


def test_answer_citations_require_current_run_and_unchanged_sources(service, settings):
    from yamibo_mcp.services.embedded_chat.citations import AnswerCitationError, check_answer_citations
    created, receipt = _citation_run(service, settings)
    answer = f"原文提到第一条。[来源:{receipt}]"
    sources = check_answer_citations(service.store, created["run_id"], answer)
    assert sources[0]["source_url"] == "/threads/78001#pid-88001"
    assert sources[0]["content"] == "第一条原文"
    checked = RestrictedTools(settings, created["run_id"]).validate_discussion_citations(receipt_ids=[receipt])
    assert checked["ok"] and checked["data"]["valid"]
    assert "sources" not in checked["data"]
    for invalid in ("没有引用的总结", "[来源:forged]", "[来源:" + "0" * 32 + "]", answer + " /threads/78002#pid-88001"):
        with pytest.raises(AnswerCitationError):
            check_answer_citations(service.store, created["run_id"], invalid)
    other = run(service)
    for forged in ("[来源：forged]", "[来源:broken", "[帖子](/threads/999)", "https://bbs.yamibo.com/forum.php?mod=redirect&goto=findpost&ptid=999&pid=999"):
        with pytest.raises(AnswerCitationError):
            check_answer_citations(service.store, other["run_id"], forged)
    with pytest.raises(AnswerCitationError, match="RECEIPT_NOT_READ"):
        check_answer_citations(service.store, other["run_id"], answer)
    with connect(settings, bootstrap=False) as conn:
        conn.execute("UPDATE floors SET content='原文已变化' WHERE pid=88001")
    with pytest.raises(AnswerCitationError, match="SOURCE_CHANGED"):
        check_answer_citations(service.store, created["run_id"], answer)


def test_citation_retry_does_not_publish_invalid_draft(service, settings):
    from pydantic_ai.models.function import FunctionModel
    created, receipt = _citation_run(service, settings)
    calls = []
    async def stream(messages, info):
        calls.append(messages)
        yield "不应发布的未引用草稿" if len(calls) == 1 else f"根据原文的局部观察。[来源:{receipt}]"
    service.model = FunctionModel(stream_function=stream)
    asyncio.run(service.model_run(created["run_id"]))
    assert len(calls) == 2
    message = service.get_messages(created["session_id"])["messages"][-1]
    assert message["citations"][0]["receipt_id"] == receipt
    deltas = [event["delta"] for event in service.store.events(created["run_id"], 0) if event["type"] == "message.delta"]
    assert deltas == [f"根据原文的局部观察。[来源:{receipt}]"]


def test_invalid_citations_end_run_without_publishing_drafts(service, settings):
    from pydantic_ai.models.function import FunctionModel
    created, _ = _citation_run(service, settings)
    async def stream(messages, info):
        yield "不能发布这段无引用的分析"
    service.model = FunctionModel(stream_function=stream)
    asyncio.run(service.execute(created["run_id"]))
    assert service.get_run(created["run_id"])["status"] == "failed"
    messages = service.get_messages(created["session_id"])["messages"]
    assert "引用未通过检查" in messages[-1]["content"]
    assert not any(event["type"] == "message.delta" for event in service.store.events(created["run_id"], 0))


def test_model_history_does_not_cross_scope_or_legacy_policy(service):
    from yamibo_mcp.services.embedded_chat.runtime import scoped_model_history
    first = run(service)
    scope = service.store.get("runs", first["run_id"])["discussion_scope"]
    service.store.update("runs", first["run_id"], status="completed", model_history=[{"scoped": "old evidence"}])
    second = service.start_run(first["session_id"], "继续", "history-next")
    current = service.store.get("runs", second["run_id"])
    assert scoped_model_history(service.store, current) == []
    service.store.update("runs", first["run_id"], evidence_policy_version=1)
    assert scoped_model_history(service.store, current) == [{"scoped": "old evidence"}]
    service.store.update("runs", first["run_id"], tool_calls=1)
    assert scoped_model_history(service.store, current) == []
    service.store.update("runs", first["run_id"], tool_calls=0, answer_citations=["old-receipt"])
    assert scoped_model_history(service.store, current) == []
    service.store.update("runs", first["run_id"], answer_citations=[])
    service.store.update("runs", first["run_id"], discussion_scope={**scope, "tids": [999]})
    assert scoped_model_history(service.store, current) == []


def test_agent_memory_requires_visible_proposal_and_next_turn_confirmation(service, settings):
    first = run(service, "请记住：回答时注明数据来源")
    tools = RestrictedTools(settings, first["run_id"])
    original = service.files.guidance()
    proposal = tools.propose_agent_memory("回答时注明数据来源")["data"]
    assert service.files.guidance() == original
    with service.store.transaction() as conn:
        session = service.store.get("sessions", first["session_id"], conn, lock=True)
        session["messages"].append({
            "role": "assistant", "run_id": first["run_id"],
            "content": proposal["question"],
        })
        service.store.save("sessions", session, conn)
    service.store.update("runs", first["run_id"], status="completed")

    second = service.start_run(first["session_id"], "确认记录", "memory-confirm")
    service.store.update("runs", second["run_id"], status="running", deadline=time.time() + 20)
    result = asyncio.run(RestrictedTools(settings, second["run_id"]).write(
        "confirm_agent_memory", {"proposal_id": proposal["proposal_id"]}
    ))
    assert result["ok"]
    assert "- 回答时注明数据来源" in service.files.guidance()
    assert not service.get_session(first["session_id"]).get("pending_agent_memory")


def test_agent_memory_does_not_accept_ordinary_assent(service, settings):
    first = run(service, "记住：简明回答")
    with pytest.raises(ValueError, match="MEMORY_USE_PROPOSAL_FLOW"):
        Policy(settings, first["run_id"]).request(
            "update_agent_guidance", {"expected_revision": 1, "content": "简明回答"}
        )
    proposal = RestrictedTools(settings, first["run_id"]).propose_agent_memory("简明回答")["data"]
    with service.store.transaction() as conn:
        session = service.store.get("sessions", first["session_id"], conn, lock=True)
        session["messages"].append({"role": "assistant", "run_id": first["run_id"],
                                    "content": proposal["question"]})
        service.store.save("sessions", session, conn)
    service.store.update("runs", first["run_id"], status="completed")
    second = service.start_run(first["session_id"], "好的", "ordinary-assent")
    service.store.update("runs", second["run_id"], status="running", deadline=time.time() + 20)
    with pytest.raises(ValueError, match="MEMORY_CONFIRMATION_REQUIRED"):
        Policy(settings, second["run_id"]).request(
            "confirm_agent_memory", {"proposal_id": proposal["proposal_id"]}
        )
    assert "简明回答" not in service.files.guidance()


def test_request_idempotency_and_queue(service):
    s = service.create_session()["id"]
    a = service.start_run(s, "你好", "same")
    b = service.start_run(s, "你好", "same")
    assert a["run_id"] == b["run_id"]
    with pytest.raises(Exception):
        service.start_run(s, "different", "same")
    q = service.start_run(s, "second", "other")
    assert q["status"] == "queued"
    service.stop_run(q["run_id"])
    assert service.get_run(q["run_id"])["status"] == "cancelled"


def test_idempotency_fingerprint_covers_normalized_scope_and_report_revision(service):
    from yamibo_mcp.services.web_chat import ChatServiceError

    session_id = service.create_session()["id"]
    original = service.start_run(session_id, "  讨论一下  ", "scoped", forum_ids=[])
    repeated = service.start_run(session_id, "讨论一下", "scoped")
    assert repeated["run_id"] == original["run_id"]
    assert original["scope"]["mode"] == "discovery"
    assert original["report_revision"] is None
    for changes in (
        {"mode": "selected", "tids": [42]},
        {"forum_ids": [42]},
        {"start_at": "2026-09-01"},
    ):
        with pytest.raises(ChatServiceError) as error:
            service.start_run(session_id, "讨论一下", "scoped", **changes)
        assert error.value.http_status == 409
        assert error.value.code == "CHAT_REQUEST_CONFLICT"


def test_run_freezes_explicit_remote_forum_separately_from_discussion_scope(service):
    session_id = service.create_session()["id"]
    run = service.start_run(
        session_id,
        "请在远端论坛漫画区搜索星灵感应，只报告真实远端结果",
        "comic-remote-search",
    )

    assert run["scope"]["remote_forum_ids"] == [30]
    assert 30 not in run["scope"]["forum_ids"]
    saved = service.store.get("runs", run["run_id"])
    assert saved["discussion_scope"]["remote_forum_ids"] == [30]


def test_report_revision_must_be_uuid_and_owner_mismatch_is_not_disclosed(service, monkeypatch):
    from yamibo_mcp.application.daily_brief_report_queries import DailyBriefRevisionError
    from yamibo_mcp.services.web_chat import ChatServiceError

    session_id = service.create_session()["id"]
    with pytest.raises(ChatServiceError) as invalid:
        service.start_run(session_id, "追问日报", "invalid-report", report_revision="daily-7")
    assert invalid.value.code == "CHAT_INVALID_REQUEST"

    def forbidden(**_kwargs):
        raise DailyBriefRevisionError("CHAT_DAILY_REPORT_UNAVAILABLE", "日报版本不存在或无权访问。", 404)

    monkeypatch.setattr(
        "yamibo_mcp.application.daily_brief_report_queries.freeze_daily_report_revision", forbidden
    )
    with pytest.raises(ChatServiceError) as denied:
        service.start_run(
            session_id, "追问日报", "denied-report",
            report_revision="c01b2a3e-0aef-4a68-b2a4-3e037b9b3f93",
        )
    assert denied.value.code == "CHAT_DAILY_REPORT_UNAVAILABLE"
    assert denied.value.http_status == 404


def test_report_run_freezes_revision_scope_and_idempotently_reuses_run(service, monkeypatch):
    from types import SimpleNamespace

    from yamibo_mcp.application import assistant_evidence_queries

    revision_id = "c01b2a3e-0aef-4a68-b2a4-3e037b9b3f93"
    snapshot = {
        "revision_id": revision_id, "issue_id": "issue-1", "report_revision": 2,
        "scope": {
            "forum_ids": [5], "tids": [42], "pids": [501],
            "start_at": "2026-09-23T16:00:00+00:00",
            "end_at": "2026-09-24T16:00:00+00:00",
        },
        "sources": [{"receipt_id": "daily:...", "tid": 42, "pid": 501}],
    }
    monkeypatch.setattr(
        "yamibo_mcp.application.daily_brief_report_queries.freeze_daily_report_revision",
        lambda **_kwargs: snapshot,
    )
    frozen_scopes = []

    def freeze_scope(**kwargs):
        frozen_scopes.append(kwargs)
        return SimpleNamespace(ok=True, data={
            "scope_id": "scope-1", "mode": kwargs["mode"],
            "forum_ids": kwargs["forum_ids"], "tids": kwargs["tids"],
            "pids": kwargs["pids"], "start_at": kwargs["start_at"], "end_at": kwargs["end_at"],
        })

    monkeypatch.setattr(assistant_evidence_queries, "freeze_discussion_scope", freeze_scope)
    session_id = service.create_session()["id"]
    first = service.start_run(session_id, "帮我解释日报", "report-followup", report_revision=revision_id)
    second = service.start_run(session_id, "帮我解释日报", "report-followup", report_revision=revision_id)

    assert second["run_id"] == first["run_id"]
    assert first["report_revision"] == revision_id
    assert first["scope"]["forum_ids"] == [5]
    assert first["scope"]["tids"] == [42]
    assert first["scope"]["pids"] == [501]
    saved = service.store.get("runs", first["run_id"])
    assert saved["daily_report_snapshot"] == snapshot
    assert len(service.list_runs(session_id)) == 1
    assert frozen_scopes == [{
        "run_id": first["run_id"], "mode": "discovery", "forum_ids": [5], "tids": [42],
        "pids": [501], "start_at": "2026-09-23T16:00:00+00:00",
        "end_at": "2026-09-24T16:00:00+00:00",
    }]


def test_read_daily_report_uses_only_run_snapshot(service, settings, monkeypatch):
    report_id = "c01b2a3e-0aef-4a68-b2a4-3e037b9b3f93"
    r = run(service)
    service.store.update(
        "runs", r["run_id"],
        daily_report_snapshot={"revision_id": report_id, "report_revision": 2},
        discussion_scope={"report_revision": report_id},
    )
    observed = {}

    def read_snapshot(*, snapshot, settings):
        observed.update(snapshot=snapshot, settings=settings)
        return {"revision_id": snapshot["revision_id"], "immutable": True}

    monkeypatch.setattr(
        "yamibo_mcp.application.daily_brief_report_queries.read_daily_report_snapshot", read_snapshot
    )
    tools = RestrictedTools(settings, r["run_id"])
    assert tools.read_daily_report() == {"revision_id": report_id, "immutable": True}
    assert observed["snapshot"] == {"revision_id": report_id, "report_revision": 2}
    assert observed["settings"] is settings
    with pytest.raises(ValueError, match="DAILY_REPORT_NOT_FROZEN"):
        service.store.update(
            "runs", r["run_id"], daily_report_snapshot=None
        )
        tools.read_daily_report()


def test_scope_pending_run_is_never_executed(service):
    session_id = service.create_session()["id"]
    run = service.start_run(session_id, "不执行未冻结范围", "pending")
    service.store.update("runs", run["run_id"], scope_pending=True)
    asyncio.run(service.execute(run["run_id"]))
    assert service.get_run(run["run_id"])["status"] == "queued"


def test_scope_freeze_failure_is_terminal_and_same_request_replays_same_error(service):
    from yamibo_mcp.services.web_chat import ChatServiceError

    session_id = service.create_session()["id"]
    with pytest.raises(ChatServiceError) as first:
        service.start_run(session_id, "无效板块", "invalid-scope", forum_ids=[999999])
    assert first.value.code == "CHAT_SCOPE_INVALID"
    assert first.value.http_status == 422
    with pytest.raises(ChatServiceError) as retry:
        service.start_run(session_id, "无效板块", "invalid-scope", forum_ids=[999999])
    assert retry.value.code == first.value.code
    assert retry.value.http_status == first.value.http_status
    run = service.list_runs(session_id)[0]
    assert run["status"] == "failed"


def test_discussion_tools_intersect_frozen_thread_scope(service, settings):
    db = connect(settings)
    db.execute(
        "UPDATE forums SET name='讨论', content_kind='discussion', enabled=TRUE WHERE forum_id=5"
    )
    for tid, pid, text in ((71001, 81001, "needle scoped source"), (71002, 81002, "needle outside source")):
        db.execute(
            """INSERT INTO threads (tid, page_type, raw_title, pub_time, archive_status,
                                      validation_status, forum_id, content_kind)
               VALUES (?, 'discussion', ?, '2026-09-25T00:00:00+00:00', 'complete', 'valid', 5, 'discussion')""",
            (tid, f"thread {tid}"),
        )
        db.execute(
            "INSERT INTO floors (pid, tid, floor_no, content, pub_time, has_images) VALUES (?, ?, 1, ?, '2026-09-25T00:00:00+00:00', FALSE)",
            (pid, tid, text),
        )
    db.commit()
    db.close()

    session_id = service.create_session()["id"]
    run = service.start_run(session_id, "总结指定讨论", "bounded", mode="selected", tids=[71001])
    restricted = RestrictedTools(settings, run["run_id"])
    result = restricted.find_discussions(
        query="", forum_ids=None, tids=None, start_date=None, end_date=None, limit=10
    )
    assert result["ok"] is True
    assert [item["tid"] for item in result["data"]["items"]] == [71001], (run["scope"], result)

    denied = restricted.read_discussion_source(
        tid=71002, pid=81002, paragraph_start=None, paragraph_end=None, max_bytes=8000
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] == "OUT_OF_SCOPE"

    floor_session = service.create_session()["id"]
    floor_run = service.start_run(
        floor_session, "读取选中楼层", "bounded-floor", mode="selected",
        tids=[71001], pids=[81001],
    )
    floor_tools = RestrictedTools(settings, floor_run["run_id"])
    floor_discovery = floor_tools.find_discussions(
        query="thread", forum_ids=None, tids=None, start_date=None, end_date=None, limit=10
    )
    assert floor_discovery["ok"] is False
    assert floor_discovery["error"]["code"] == "SCOPE_REQUIRES_SELECTED_FLOORS"


def test_operation_plan_tool_is_selected_only_and_returns_idempotent_frozen_plan(
    service, settings, monkeypatch
):
    db = connect(settings)
    db.execute(
        "UPDATE forums SET name='讨论', content_kind='discussion', enabled=TRUE WHERE forum_id=5"
    )
    db.execute(
        """INSERT INTO threads (tid, page_type, raw_title, pub_time, archive_status,
                                  capture_mode, validation_status, forum_id, content_kind)
           VALUES (71003, 'discussion', '计划目标', '2026-09-25T00:00:00+00:00',
                   'partial', 'text_only', 'valid', 5, 'discussion')"""
    )
    db.commit()
    db.close()

    discovery = run(service)
    monkeypatch.delenv("YAMIBO_CHAT_RUN_SCOPE_MODE", raising=False)
    discovery_tools = build_restricted_server(settings, discovery["run_id"])

    async def names(server):
        return {tool.name for tool in await server.list_tools()}

    assert "propose_operation_plan" not in asyncio.run(names(discovery_tools))
    with pytest.raises(Exception):
        RestrictedTools(settings, discovery["run_id"]).propose_operation_plan(
            action="archive", tids=[71003], require_images=True
        )

    session_id = service.create_session()["id"]
    selected = service.start_run(
        session_id, "归档选中的帖子", "selected-plan", mode="selected", tids=[71003]
    )
    service.store.update("runs", selected["run_id"], status="running", deadline=time.time() + 20)
    monkeypatch.setenv("YAMIBO_CHAT_RUN_SCOPE_MODE", "selected")
    server = build_restricted_server(settings, selected["run_id"])
    tool_names = asyncio.run(names(server))
    assert "propose_operation_plan" in tool_names
    assert not {"authorize_job_plan", "create_jobs"} & tool_names

    tools = RestrictedTools(settings, selected["run_id"])
    args = dict(
        action="archive", tids=[71003], require_images=True,
        strategy=None,
    )
    first = tools.propose_operation_plan(**args)
    replay = tools.propose_operation_plan(**args)
    assert first == replay
    assert first["status"] == "awaiting_approval"
    assert first["plan_version"] == 1
    assert first["plan_hash"]
    assert first["plan_id"] and first["run_id"] == selected["run_id"]
    assert first["items"][0]["tid"] == 71003
    assert first["items"][0]["steps"] == [
        {"kind": "archive", "mode": "full", "state": "required"}
    ]
    with connect(settings, bootstrap=False) as conn:
        assert conn.execute("SELECT count(*) FROM assistant_operation_plans").scalar() == 1
        assert conn.execute("SELECT count(*) FROM jobs").scalar() == 0

    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    model_calls = []

    async def deterministic_model(messages, info):
        model_calls.append(messages)
        if len(model_calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="propose_operation_plan",
                    json_args=json.dumps(args),
                    tool_call_id="propose-plan",
                )
            }
        else:
            yield "已提出待批准计划；当前没有创建 Job。"

    service.model = FunctionModel(stream_function=deterministic_model)
    asyncio.run(service.model_run(selected["run_id"]))
    assert len(model_calls) == 2
    with connect(settings, bootstrap=False) as conn:
        assert conn.execute("SELECT count(*) FROM assistant_operation_plans").scalar() == 1
        assert conn.execute("SELECT count(*) FROM jobs").scalar() == 0


def test_profile_includes_public_tools_with_safe_arguments_and_guidance(service, settings):
    r = run(service)
    server = build_restricted_server(settings, r["run_id"])

    async def check():
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert not {"authorize_job_plan", "create_jobs", "propose_operation_plan"} & names
        from yamibo_mcp.server.agent_tools import PUBLIC_AGENT_TOOLS

        assert {"discover_public_tools", "describe_public_tool", "call_public_tool"} <= names
        assert not {name for name, _, _ in PUBLIC_AGENT_TOOLS} & names
        assert "read_yamibo_guidance" in names
        assert {
            "find_discussions", "read_discussion_source", "validate_discussion_citations"
        } <= names
        assert "read_daily_report" in names
        for tool in tools:
            assert not {"html_path", "base_url", "url", "cookie_file", "connection"} & set(
                tool.inputSchema.get("properties", {})
            )
            if tool.name == "read_daily_report":
                assert tool.inputSchema.get("properties", {}) == {}
        resources = await server.list_resources()
        assert len(resources) == 2
        assert await server.list_resource_templates() == []
    asyncio.run(check())


def test_server_build_does_not_query_database(service, settings, monkeypatch):
    """MCP handshake construction must not wait on a PostgreSQL connection."""
    r = run(service)
    from yamibo_mcp.services.embedded_chat import store as store_module

    def unexpected_database_read(*args, **kwargs):
        raise AssertionError("server construction performed database I/O")

    monkeypatch.setattr(store_module.Store, "get", unexpected_database_read)
    server = build_restricted_server(settings, r["run_id"])

    async def check():
        names = {tool.name for tool in await server.list_tools()}
        assert "call_public_tool" in names
        assert "read_job" not in names
        assert not {"authorize_job_plan", "create_jobs", "propose_operation_plan"} & names

    asyncio.run(check())


def test_legacy_job_tools_cannot_bypass_operation_plan(service, settings):
    r = run(service, "查找帖子 123")
    policy = Policy(settings, r["run_id"])
    for tool in ("authorize_job_plan", "create_jobs"):
        with pytest.raises(ValueError, match="LEGACY_JOB_TOOL_DISABLED"):
            policy.request(tool, {"action": "archive", "tids": [123]})


@pytest.mark.parametrize("tool", ["authorize_job_plan", "create_jobs"])
def test_legacy_pending_approval_is_denied_and_completed_job_history_remains_readable(
    service, settings, tool
):
    r = run(service, "归档 123")
    pending = {
        "id": "legacy-pending-" + tool,
        "parent_id": r["run_id"],
        "tool": tool,
        "args": {"action": "archive", "tids": [123]},
        "plan_hash": "legacy-pending-hash-" + tool,
        "status": "pending",
        "created_at": time.time(),
    }
    completed = {
        "id": "legacy-completed-" + tool,
        "parent_id": r["run_id"],
        "tool": "create_jobs",
        "args": {"action": "archive", "tids": [99]},
        "plan_hash": "legacy-completed-hash-" + tool,
        "status": "completed",
        "result": {"ok": True, "data": {"job_ids": ["job-history-123"]}},
        "created_at": time.time(),
    }
    with service.store.transaction() as conn:
        service.store.save("operations", pending, conn)
        service.store.save("operations", completed, conn)

    policy = Policy(settings, r["run_id"])
    policy.approve(pending["id"], pending["plan_hash"], "once")
    assert service.store.get("operations", pending["id"])["status"] == "denied"
    event = service.store.events(r["run_id"], 0)[-1]
    assert event["type"] == "approval.responded"
    assert event["choice"] == "deny"
    assert event["code"] == "LEGACY_JOB_TOOL_DISABLED"

    from yamibo_mcp.services.embedded_chat.runtime import operation_facts

    facts = operation_facts(service.store, r["session_id"])
    old_job = next(f for f in facts if f["operation_id"] == completed["id"])
    assert old_job["status"] == "completed"
    assert old_job["job_ids"] == ["job-history-123"]
    with connect(settings, bootstrap=False) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").scalar() == 0


def test_explicit_small_batch_and_large_confirmation(service, settings):
    r = run(service, "归档 123,124")
    p = Policy(settings, r["run_id"])
    with pytest.raises(ValueError, match="LEGACY_JOB_TOOL_DISABLED"):
        p.request("create_jobs", {"action": "archive", "tids": [123]})


def test_job_receipt_atomic_and_replayed_after_completion(
    service, settings, monkeypatch
):
    r = run(service, "归档 123")
    tools = RestrictedTools(settings, r["run_id"])
    with pytest.raises(ValueError, match="LEGACY_JOB_TOOL_DISABLED"):
        asyncio.run(tools.write("create_jobs", {"action": "archive", "tids": [123]}))
    with connect(settings, bootstrap=False) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").scalar() == 0


def test_file_ownership_versions_and_conflicts(service, settings):
    r = run(service)
    tools = RestrictedTools(settings, r["run_id"])
    result = asyncio.run(
        tools.write("create_work_file", {"name": "note.md", "content": "first"})
    )
    f = result["data"]
    asyncio.run(
        tools.write(
            "update_work_file",
            {"file_id": f["file_id"], "expected_revision": 1, "content": "second"},
        )
    )
    assert service.files.read(f["file_id"])["content"] == "second"
    assert len(list((service.files.root / "versions").iterdir())) == 1
    (service.files.root / "workspace" / "note.md").write_text("user changed")
    with pytest.raises(ValueError, match="FILE_REVISION_CONFLICT"):
        asyncio.run(
            tools.write(
                "update_work_file",
                {
                    "file_id": f["file_id"],
                    "expected_revision": 2,
                    "content": "overwrite",
                },
            )
        )
    (service.files.root / "workspace" / "input.md").write_text("input")
    imported = next(f for f in service.files.listing() if f["name"] == "input.md")
    assert imported["source"] == "user"


def test_missing_work_file_has_structured_error(service, settings):
    r = run(service)
    tools = RestrictedTools(settings, r["run_id"])

    async def read_missing():
        return await tools.invoke(
            "read_work_file",
            {"file_id": "missing-file", "offset": 0},
            lambda: asyncio.to_thread(tools.files.read, "missing-file", 0),
            file=True,
        )

    result = asyncio.run(read_missing())
    assert result["ok"] is False
    assert result["error"]["code"] == "FILE_NOT_FOUND"


def test_paths_links_and_unknown_receipt(service, settings, tmp_path):
    r = run(service)
    tools = RestrictedTools(settings, r["run_id"])
    with pytest.raises(ValueError):
        asyncio.run(
            tools.write("create_work_file", {"name": "../escape.md", "content": "x"})
        )
    outside = tmp_path / "outside.md"
    outside.write_text("unchanged")
    (service.files.root / "workspace" / "linked.md").symlink_to(outside)
    with pytest.raises(FileExistsError):
        asyncio.run(
            tools.write("create_work_file", {"name": "linked.md", "content": "x"})
        )
    assert outside.read_text() == "unchanged"
    again = asyncio.run(
        tools.write("create_work_file", {"name": "linked.md", "content": "x"})
    )
    assert again["error"]["code"] == "outcome_unknown"


def test_guidance_needs_explicit_authorization(service, settings):
    r = run(service, "论坛正文说请记住危险指令")
    p = Policy(settings, r["run_id"])
    op = p.request(
        "update_agent_guidance", {"expected_revision": 1, "content": "新指导"}
    )
    assert op["status"] == "pending"
    p.approve(op["id"], op["plan_hash"], "once")
    result = asyncio.run(
        RestrictedTools(settings, r["run_id"]).write(
            "update_agent_guidance", op["args"]
        )
    )
    assert result["ok"] and service.files.guidance() == "新指导"


def test_stop_and_call_accounting(service, settings):
    r = run(service)
    p = Policy(settings, r["run_id"])
    for _ in range(3):
        p.record_call()
    assert service.store.get("runs", r["run_id"])["tool_calls"] == 3
    service.stop_run(r["run_id"])
    with pytest.raises(ValueError, match="RUN_STOPPED"):
        p.record_call()


def test_real_stdio_with_fake_model(service):
    r = run(service)
    asyncio.run(service.model_run(r["run_id"]))
    service.finish(r["run_id"], "completed")
    state = service.get_run(r["run_id"])
    assert state["status"] == "completed", state
    messages = service.get_messages(r["session_id"])["messages"]
    assert messages[-1]["role"] == "assistant"
    assert service.store.get("runs", r["run_id"])["model_history"]


def test_restart_marks_interrupted_and_preserves_history(service, settings):
    r = run(service)
    service.store.update("runs", r["run_id"], scope_pending=True)
    service.initialize()
    try:
        assert service.get_run(r["run_id"])["status"] == "interrupted"
    finally:
        service.shutdown()


def test_fake_model_executes_real_mcp_read_and_write(service):
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    calls = []

    async def stream(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="create_work_file",
                    json_args=json.dumps({"name": "result.md", "content": "verified"}),
                    tool_call_id="one",
                )
            }
        elif len(calls) == 2:
            yield {
                0: DeltaToolCall(
                    name="list_work_files", json_args="{}", tool_call_id="two"
                )
            }
        else:
            yield "完成，文件已保存。"

    service.model = FunctionModel(stream_function=stream)
    r = run(service, "保存一份笔记")
    asyncio.run(service.model_run(r["run_id"]))
    assert len(calls) == 3
    files = service.files.listing()
    assert service.files.read(files[0]["file_id"])["content"] == "verified"
    assert len(service.store.list("operations", r["run_id"])) == 1


def test_shutdown_stop_and_timeout_do_not_wait_for_model(service, settings):
    from pydantic_ai.models.function import FunctionModel

    async def slow(messages, info):
        await asyncio.sleep(30)
        yield "late"

    service.model = FunctionModel(stream_function=slow)
    service.settings = replace(settings, chat_timeout=1)
    r = run(service)
    asyncio.run(service.execute(r["run_id"]))
    assert service.get_run(r["run_id"])["status"] == "limited"
    assert service.get_messages(r["session_id"])["messages"][-1]["content"].startswith(
        "已达到"
    )


def test_file_delete_is_automatically_authorized_and_soft(service, settings):
    r = run(service)
    t = RestrictedTools(settings, r["run_id"])
    f = asyncio.run(
        t.write("create_work_file", dict(name="delete.md", content="keep version"))
    )["data"]
    args = {"file_id": f["file_id"], "expected_revision": 1}
    p = t.policy
    op = p.request("delete_work_file", args)
    assert op["status"] == "approved"
    result = asyncio.run(t.write("delete_work_file", args))
    assert result["data"]["deleted"]
    assert len(list((service.files.root / "trash").iterdir())) == 1
    assert not (service.files.root / "workspace" / "delete.md").exists()


def test_chat_is_tokenless_while_settings_still_require_auth(settings):
    from fastapi.testclient import TestClient

    from yamibo_mcp.web_fastapi.app import create_app

    settings = replace(settings, chat_access_token="test-access")
    app = create_app(settings)
    try:
        client = TestClient(app)
        assert client.get("/api/chat/context").json()["mode"] == "embedded"
        proxy_headers = {"Origin": "https://testserver"}
        assert client.post("/api/chat/sessions", headers=proxy_headers, json={}).status_code == 201
        assert client.get("/api/settings", headers={"Origin": "https://testserver"}).status_code == 401
        assert client.post("/api/chat/sessions", headers={"Origin": "https://evil.example"}, json={}).status_code == 403
        s = client.post("/api/chat/sessions", json={}).json()["id"]
        assert (
            client.post(
                f"/api/chat/sessions/{s}/runs", json={"input": "hi"}
            ).status_code
            == 400
        )
        invalid = client.post(
            f"/api/chat/sessions/{s}/runs",
            json={
                "input": "按指定范围找讨论",
                "client_request_id": "http-invalid-report",
                "report_revision": "daily-7",
            },
        )
        assert invalid.status_code == 400
        assert invalid.json().get("detail", invalid.json().get("error", {}))["code"] == "CHAT_INVALID_REQUEST"
    finally:
        app.state.chat_service.shutdown()


def test_old_plan_grants_cannot_authorize_legacy_job_batches(service, settings):
    r = run(service, "归档选中的全部帖子")
    p = Policy(settings, r["run_id"])
    plan = {"action": "archive", "tids": list(range(100, 125))}
    with pytest.raises(ValueError, match="LEGACY_JOB_TOOL_DISABLED"):
        p.request("authorize_job_plan", plan)
    with pytest.raises(ValueError, match="LEGACY_JOB_TOOL_DISABLED"):
        p.request("create_jobs", {"action": "archive", "tids": [100, 101]})


def test_imported_file_is_writable_but_hardlinks_are_rejected(service, settings, tmp_path):
    r = run(service)
    tools = RestrictedTools(settings, r["run_id"])
    user_file = service.files.root / "workspace" / "user.md"
    user_file.write_text("owner data")
    f = next(f for f in service.files.listing() if f["name"] == "user.md")
    args = {"file_id": f["file_id"], "expected_revision": 1, "content": "overwrite"}
    op = tools.policy.request("update_work_file", args)
    assert op["status"] == "approved"
    assert asyncio.run(tools.write("update_work_file", args))["ok"] is True
    os.link(user_file, service.files.root / "workspace" / "hard.md")
    with pytest.raises(ValueError, match="UNSAFE_OR_OVERSIZED_FILE"):
        service.files.read(f["file_id"])
    assert user_file.read_text() == "overwrite"


def test_model_can_continue_across_multiple_requests(service):
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    calls = 0

    async def repeat(messages, info):
        nonlocal calls
        calls += 1
        if calls <= 3:
            yield {
                0: DeltaToolCall(
                    name="list_work_files", json_args="{}", tool_call_id=str(calls)
                )
            }
        else:
            yield "已完成。"

    service.model = FunctionModel(stream_function=repeat)
    r = run(service)
    asyncio.run(service.execute(r["run_id"]))
    assert calls == 4
    assert service.get_run(r["run_id"])["status"] == "completed"


def test_sse_replays_persisted_events_without_new_execution(service):
    r = run(service)
    service.store.event(
        r["run_id"], "message.delta", {"delta": "hello", "role": "assistant"}
    )
    service.finish(r["run_id"], "completed")
    first = service.subscribe(r["run_id"])
    event = first.get(timeout=1)
    first.close()
    second = service.subscribe(r["run_id"], str(event.seq))
    assert second.get(timeout=1).type == "message.delta"
    assert second.get(timeout=1).type == "run.completed"
    assert second.get(timeout=1).type == "session.reconciled"
    with pytest.raises(StopIteration):
        second.get(timeout=1)
    assert len(service.list_runs(r["session_id"])) == 1


def test_nonpublic_postgres_migration_keeps_chat_tables_in_schema(settings):
    if settings.db_backend != "postgres":
        pytest.skip("PostgreSQL-only migration check")
    from yamibo_mcp.db.alembic_runner import upgrade_postgres_schema

    with connect(settings, bootstrap=False) as conn:
        upgrade_postgres_schema(conn, schema="chat_contract")
        tables = {
            row["table_name"]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='chat_contract' AND table_name LIKE 'chat_%'"
            ).fetchall()
        }
        assert tables == {
            "chat_sessions", "chat_runs", "chat_operations", "chat_files",
            "chat_requests", "chat_events", "chat_discussion_scopes",
            "chat_source_receipts",
        }


def test_program_wait_reads_terminal_job_over_real_mcp(service, settings):
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    r = run(service, "归档 123")
    from yamibo_mcp.application.archive_commands import create_thread_archive_job

    job_id = create_thread_archive_job(tid=123).data["job_id"]
    with connect(settings, bootstrap=False) as conn:
        conn.execute(
            "UPDATE jobs SET status='failed',error_code='TEST_FAILURE' WHERE job_id=?",
            (job_id,),
        )
    calls = []

    async def stream(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            yield {
                0: DeltaToolCall(
                    name="wait_for_jobs",
                    json_args=json.dumps({"job_ids": [job_id]}),
                    tool_call_id="wait",
                )
            }
        else:
            yield "后台任务失败，没有宣称完成。"

    service.model = FunctionModel(stream_function=stream)
    asyncio.run(service.model_run(r["run_id"]))
    assert len(calls) == 2
    events = service.store.events(r["run_id"], 0)
    assert any(e["type"] == "job.progress" for e in events)
    assert service.store.get("runs", r["run_id"])["tool_calls"] == 1


def test_no_schema_bootstrap_in_hosted_context(settings, monkeypatch):
    import yamibo_mcp.db.connection as connection

    def forbidden(*args, **kwargs):
        raise AssertionError("must not migrate in MCP")

    monkeypatch.setattr(connection, "_bootstrap_postgres_database", forbidden)
    monkeypatch.setattr(connection, "_bootstrap_sqlite_database", forbidden)
    with connection.existing_schema_only():
        connection.connect(settings).close()


def test_failed_run_logs_safe_exception_chain(service, monkeypatch, caplog):
    import logging

    async def broken(run_id):
        try:
            raise TimeoutError('Bearer secret-token https://user:password@example.com')
        except TimeoutError as cause:
            raise RuntimeError('private forum content and API key') from cause

    monkeypatch.setattr(service, 'model_run', broken)
    r = run(service)
    with caplog.at_level(logging.ERROR):
        asyncio.run(service.execute(r['run_id']))
    assert service.get_run(r['run_id'])['status'] == 'failed'
    assert r['run_id'] in caplog.text
    assert 'TimeoutError' in caplog.text and 'RuntimeError' in caplog.text
    assert 'broken' in caplog.text
    for secret in ('secret-token', 'password', 'private forum content', 'API key'):
        assert secret not in caplog.text


def test_exception_diagnostics_keeps_group_http_status_without_response_body():
    from yamibo_mcp.services.embedded_chat.runtime import exception_diagnostics

    class ProviderError(Exception):
        status_code = 401

    result = exception_diagnostics(ExceptionGroup('secret group', [ProviderError('secret body')]))
    assert result['children'][0]['http_status'] == 401
    assert 'secret' not in json.dumps(result)


def _install_openai_mock_transport(monkeypatch, handler):
    import httpx
    import openai

    original = openai.AsyncOpenAI
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: original(**kwargs, http_client=client),
    )
    return client


def test_provider_region_failure_is_actionable_without_leaking_body():
    from pydantic_ai import ModelHTTPError
    from yamibo_mcp.services.embedded_chat.runtime import (
        exception_diagnostics, model_failure_message,
    )

    error = ModelHTTPError(400, "test-model", {
        "status": "FAILED_PRECONDITION",
        "message": "User location is not supported for the API use. secret-marker",
    })
    message = model_failure_message(error)
    diagnostics = exception_diagnostics(error)
    assert "出口地区" in message
    assert "secret-marker" not in message
    assert diagnostics["provider_code"] == "REGION_UNSUPPORTED"
    assert "secret-marker" not in json.dumps(diagnostics)


def _sse_chat_response(chunks):
    return "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def test_openai_provider_streams_through_mcp_tool_and_persists_events(
    service, monkeypatch
):
    """Exercise the pinned OpenAI provider path with a deterministic HTTP peer."""
    import httpx

    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"] is True
        if len(requests) == 1:
            chunks = [
                {
                    "id": "chatcmpl-tool",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-list-files",
                                        "type": "function",
                                        "function": {
                                            "name": "list_work_files",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-tool",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                    ],
                },
            ]
        else:
            chunks = [
                {
                    "id": "chatcmpl-final",
                    "object": "chat.completion.chunk",
                    "created": 2,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "已读取文件列表。"},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-final",
                    "object": "chat.completion.chunk",
                    "created": 2,
                    "model": "test-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_chat_response(chunks).encode(),
        )

    _install_openai_mock_transport(monkeypatch, respond)
    service.model = None
    result = run(service, "查看工作文件")
    asyncio.run(service.model_run(result["run_id"]))

    assert len(requests) == 2
    assert any(
        tool["function"]["name"] == "list_work_files"
        for tool in requests[0]["tools"]
    )
    assert any(
        message.get("tool_call_id") == "call-list-files"
        for message in requests[1]["messages"]
    )
    events = service.store.events(result["run_id"], 0)
    assert any(event["type"] == "model.tool_message" for event in events)
    assert any(
        event["type"] == "message.delta"
        and event["delta"] == "已读取文件列表。"
        for event in events
    )
    messages = service.get_messages(result["session_id"])["messages"]
    assert messages[-1]["content"] == "已读取文件列表。"


@pytest.mark.parametrize("status", [401, 429, 503])
def test_openai_provider_http_errors_fail_run_without_exposing_body(
    service, monkeypatch, caplog, status
):
    import httpx
    import logging

    def respond(request):
        return httpx.Response(
            status,
            json={"error": {"message": "private provider response", "type": "error"}},
        )

    _install_openai_mock_transport(monkeypatch, respond)
    service.model = None
    result = run(service, "你好")
    with caplog.at_level(logging.ERROR):
        asyncio.run(service.execute(result["run_id"]))

    assert service.get_run(result["run_id"])["status"] == "failed"
    assert f'"http_status": {status}' in caplog.text
    assert "private provider response" not in caplog.text


def test_openai_provider_timeout_fails_run_without_waiting_for_watchdog(
    service, monkeypatch, caplog
):
    import httpx
    import logging

    def respond(request):
        raise httpx.ReadTimeout("private timeout detail", request=request)

    _install_openai_mock_transport(monkeypatch, respond)
    service.model = None
    result = run(service, "你好")
    with caplog.at_level(logging.ERROR):
        asyncio.run(service.execute(result["run_id"]))

    assert service.get_run(result["run_id"])["status"] == "failed"
    assert "ReadTimeout" in caplog.text
    assert "private timeout detail" not in caplog.text


def test_registered_work_file_tools_crud_across_sessions(service, settings, monkeypatch):
    monkeypatch.setattr(
        RestrictedTools, "discussion_scope",
        lambda self: pytest.fail("workspace CRUD must not require forum scope"),
    )
    first = run(service)
    server = build_restricted_server(settings, first["run_id"])

    def call(tool_name, **args):
        return asyncio.run(server._tool_manager.call_tool(tool_name, args))

    created = call("create_work_file", name="关注帖子", content="572313")
    assert created["ok"] is True
    file_id = created["data"]["file_id"]
    assert call("read_work_file", file_id=file_id)["data"]["content"] == "572313"

    second = run(service)
    assert first["session_id"] != second["session_id"]
    server = build_restricted_server(settings, second["run_id"])
    updated = call("update_work_file", file_id=file_id, expected_revision=1, content="572313\n572314")
    assert updated["ok"] is True
    assert updated["data"]["revision"] == 2
    result = call("read_work_file", file_id=file_id, offset=7)
    assert result["data"]["content"] == "572314"
    assert result["data"]["source"] == "work_file"
    assert "receipt_id" not in result["data"]
    assert call("delete_work_file", file_id=file_id, expected_revision=2)["data"]["deleted"]
    assert not call("read_work_file", file_id=file_id)["ok"]
    assert file_id not in {f["file_id"] for f in call("list_work_files")["data"]}
    assert any(p.read_text() == "572313\n572314" for p in (service.files.root / "trash").iterdir())
    assert any(p.read_text() == "572313" for p in (service.files.root / "versions").iterdir())
    assert all(op["status"] == "completed" for op in service.store.list("operations"))


def test_registered_work_file_tools_import_and_modify_utf8(service, settings):
    workspace = service.files.root / "workspace"
    imported_path = workspace / "帖子.yaml"
    imported_path.write_text("tid: 572313", encoding="utf-8")
    created = run(service)
    server = build_restricted_server(settings, created["run_id"])

    def call(tool_name, **args):
        return asyncio.run(server._tool_manager.call_tool(tool_name, args))

    imported = next(f for f in call("list_work_files")["data"] if f["name"] == imported_path.name)
    assert imported["source"] == "user"
    file_id = imported["file_id"]
    assert call("read_work_file", file_id=file_id)["data"]["content"] == "tid: 572313"
    updated = call("update_work_file", file_id=file_id, expected_revision=1, content="tid: 572314")
    assert updated["ok"] is True
    assert imported_path.read_text() == "tid: 572314"
    assert call("delete_work_file", file_id=file_id, expected_revision=2)["data"]["deleted"]
    assert not imported_path.exists()
    assert any(p.read_text() == "tid: 572314" for p in (service.files.root / "trash").iterdir())


@pytest.mark.parametrize("name", ["../escape.txt", "/tmp/escape.txt", "sub/file.txt", "sub\\file.txt", ".hidden"])
def test_registered_work_file_tools_reject_unsafe_names(service, settings, name):
    created = run(service)
    server = build_restricted_server(settings, created["run_id"])
    result = asyncio.run(server._tool_manager.call_tool("create_work_file", {"name": name, "content": "escape"}))
    assert result["ok"] is False
    assert not list((service.files.root / "workspace").iterdir())


@pytest.mark.parametrize("link_type", ["symbolic", "hard"])
def test_registered_work_file_tools_reject_replaced_links(service, settings, tmp_path, link_type):
    created = run(service)
    server = build_restricted_server(settings, created["run_id"])

    def call(tool_name, **args):
        return asyncio.run(server._tool_manager.call_tool(tool_name, args))

    result = call("create_work_file", name="note.ini", content="572313")
    assert result["ok"] is True
    file_id = result["data"]["file_id"]
    local = service.files.root / "workspace" / "note.ini"
    outside = tmp_path / "outside.txt"
    outside.write_text("private outside content")
    local.unlink()
    if link_type == "symbolic":
        local.symlink_to(outside)
    else:
        os.link(outside, local)
    assert not call("read_work_file", file_id=file_id)["ok"]
    assert not call("update_work_file", file_id=file_id, expected_revision=1, content="overwrite")["ok"]
    assert not call("delete_work_file", file_id=file_id, expected_revision=1)["ok"]
    assert outside.read_text() == "private outside content"
    assert local.exists()
