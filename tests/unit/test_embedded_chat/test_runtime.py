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


def run(service, text="你好"):
    s = service.create_session()
    r = service.start_run(s["id"], text, "request-1")
    service.store.update(
        "runs", r["run_id"], status="running", deadline=time.time() + 20
    )
    return r


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


def test_profile_excludes_unsafe_arguments_and_resources(service, settings):
    r = run(service)
    server = build_restricted_server(settings, r["run_id"])

    async def check():
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert "create_jobs" in names and "create_rag_index_job" not in names
        for tool in tools:
            assert not {"html_path", "base_url", "url", "connection"} & set(
                tool.inputSchema.get("properties", {})
            )
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
        assert "read_job" in names and "create_jobs" in names

    asyncio.run(check())


def test_authorization_scope_and_stale_approval(service, settings):
    r = run(service, "查找帖子 123")
    policy = Policy(settings, r["run_id"])
    op = policy.request("create_jobs", {"action": "archive", "tids": [123]})
    assert op["status"] == "pending"
    with pytest.raises(ValueError):
        policy.approve(op["id"], "wrong", "once")
    policy.approve(op["id"], op["plan_hash"], "once")
    with pytest.raises(ValueError):
        policy.approve(op["id"], op["plan_hash"], "always")
    assert (
        policy.request("create_jobs", {"action": "archive", "tids": [124]})["status"]
        == "pending"
    )


def test_explicit_small_batch_and_large_confirmation(service, settings):
    r = run(service, "归档 123,124")
    p = Policy(settings, r["run_id"])
    assert (
        p.request("create_jobs", {"action": "archive", "tids": [123]})["status"]
        == "approved"
    )
    assert (
        p.request("create_jobs", {"action": "archive", "tids": [125]})["status"]
        == "pending"
    )


def test_job_receipt_atomic_and_replayed_after_completion(
    service, settings, monkeypatch
):
    r = run(service, "归档 123")
    tools = RestrictedTools(settings, r["run_id"])
    result = asyncio.run(
        tools.write("create_jobs", {"action": "archive", "tids": [123]})
    )
    again = asyncio.run(
        tools.write("create_jobs", {"action": "archive", "tids": [123]})
    )
    assert result == again
    with connect(settings, bootstrap=False) as conn:
        assert conn.execute("SELECT count(*) FROM jobs").scalar() == 1
    from yamibo_mcp.application import archive_commands

    original = archive_commands.create_thread_archive_job

    def fail_after_insert(**kwargs):
        original(**kwargs)
        raise RuntimeError("simulated lost response before transaction commit")

    monkeypatch.setattr(
        archive_commands, "create_thread_archive_job", fail_after_insert
    )
    r2 = run(service, "归档 456")
    t2 = RestrictedTools(settings, r2["run_id"])
    with pytest.raises(RuntimeError):
        asyncio.run(t2.write("create_jobs", {"action": "archive", "tids": [456]}))
    with connect(settings, bootstrap=False) as conn:
        assert conn.execute("SELECT count(*) FROM jobs WHERE tid=456").scalar() == 0


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


def test_delete_and_guidance_need_explicit_authorization(service, settings):
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


def test_stop_and_budget(service, settings):
    r = run(service)
    p = Policy(replace(settings, chat_max_tools=1), r["run_id"])
    p.budget()
    with pytest.raises(ValueError, match="TOOL_LIMIT_REACHED"):
        p.budget()
    service.stop_run(r["run_id"])
    with pytest.raises(ValueError, match="RUN_STOPPED"):
        p.budget()


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


def test_file_delete_is_soft_and_denied_plan_cannot_replay(service, settings):
    r = run(service)
    t = RestrictedTools(settings, r["run_id"])
    f = asyncio.run(
        t.write("create_work_file", dict(name="delete.md", content="keep version"))
    )["data"]
    args = {"file_id": f["file_id"], "expected_revision": 1}
    p = t.policy
    op = p.request("delete_work_file", args)
    assert op["status"] == "pending"
    p.approve(op["id"], op["plan_hash"], "once")
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
    finally:
        app.state.chat_service.shutdown()


def test_confirm_batch_once_then_subsets_and_no_duplicate_tid(service, settings):
    r = run(service, "归档选中的全部帖子")
    p = Policy(settings, r["run_id"])
    plan = {"action": "archive", "tids": list(range(100, 125))}
    op = p.request("authorize_job_plan", plan)
    p.approve(op["id"], op["plan_hash"], "once")
    tools = RestrictedTools(settings, r["run_id"])
    asyncio.run(tools.write("authorize_job_plan", plan))
    a = asyncio.run(
        tools.write("create_jobs", {"action": "archive", "tids": [100, 101]})
    )
    b = asyncio.run(
        tools.write("create_jobs", {"action": "archive", "tids": [101, 102]})
    )
    assert a["data"]["job_ids"][1] == b["data"]["job_ids"][0]
    assert (
        p.request("create_jobs", {"action": "archive", "tids": [999]})["status"]
        == "pending"
    )


def test_hardlink_and_imported_file_are_not_writable(service, settings, tmp_path):
    r = run(service)
    tools = RestrictedTools(settings, r["run_id"])
    user_file = service.files.root / "workspace" / "user.md"
    user_file.write_text("owner data")
    f = next(f for f in service.files.listing() if f["name"] == "user.md")
    args = {"file_id": f["file_id"], "expected_revision": 1, "content": "overwrite"}
    op = tools.policy.request("update_work_file", args)
    tools.policy.approve(op["id"], op["plan_hash"], "once")
    with pytest.raises(ValueError, match="FILE_NOT_AGENT_OWNED"):
        asyncio.run(tools.write("update_work_file", args))
    os.link(user_file, service.files.root / "workspace" / "hard.md")
    with pytest.raises(ValueError, match="UNSAFE_OR_OVERSIZED_FILE"):
        service.files.read(f["file_id"])
    assert user_file.read_text() == "owner data"


def test_model_request_budget_is_enforced(service, settings):
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    async def repeat(messages, info):
        yield {
            0: DeltaToolCall(
                name="list_work_files", json_args="{}", tool_call_id=str(len(messages))
            )
        }

    service.model = FunctionModel(stream_function=repeat)
    service.settings = replace(settings, chat_max_requests=2)
    r = run(service)
    asyncio.run(service.execute(r["run_id"]))
    assert service.get_run(r["run_id"])["status"] == "limited"


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
        assert (
            conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='chat_contract' AND table_name LIKE 'chat_%'"
            ).scalar()
            == 6
        )


def test_program_wait_reads_terminal_job_over_real_mcp(service, settings):
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    r = run(service, "归档 123")
    receipt = asyncio.run(
        RestrictedTools(settings, r["run_id"]).write(
            "create_jobs", {"action": "archive", "tids": [123]}
        )
    )
    job_id = receipt["data"]["job_ids"][0]
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
