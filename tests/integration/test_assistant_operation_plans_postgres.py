from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from yamibo_mcp.application.assistant_operation_plans import (
    OperationPlanError,
    approve_operation_plan,
    create_operation_plan,
    list_operation_plans,
)
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.assistant_operation_plans import AssistantOperationPlansRepository


def test_postgres_plan_approval_is_atomic_idempotent_and_concurrency_safe(pg_engine, monkeypatch):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    suffix = uuid.uuid4().hex[:10]
    tid, forum_id = 960_000_000 + int(suffix, 16) % 10_000_000, 398_000 + int(suffix, 16) % 10_000
    session_id, other_session_id, run_id, scope_id = f"op-session-{suffix}", f"op-other-session-{suffix}", f"op-run-{suffix}", f"op-scope-{suffix}"
    db_path = ":isolated-operation-plan-test:"
    with pg_engine.connect() as raw:
        migrate(DatabaseConnection(raw, backend="postgres"), schema="public")
    with pg_engine.begin() as raw:
        raw.execute(text("INSERT INTO forums(forum_id,name,content_kind,base_url,enabled) VALUES(:fid,'operation plan test','discussion','https://example.invalid',TRUE) ON CONFLICT(forum_id) DO UPDATE SET content_kind='discussion',enabled=TRUE"), {"fid": forum_id})
        raw.execute(text("""INSERT INTO threads(tid,page_type,raw_title,pub_time,sync_time,last_pid,archive_status,capture_mode,validation_status,forum_id,content_kind)
            VALUES(:tid,'discussion','operation plan test','2026-09-25 08:00:00+00','2026-09-25 00:00:00+00',:pid,'complete','text_only','valid',:fid,'discussion')"""), {"tid": tid, "pid": tid + 1, "fid": forum_id})
        raw.execute(text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"), {"id": session_id, "data": json.dumps({"id": session_id, "owner_id": "local"})})
        raw.execute(text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"), {"id": other_session_id, "data": json.dumps({"id": other_session_id, "owner_id": "other-owner"})})
        raw.execute(text("INSERT INTO chat_runs(id,parent_id,data) VALUES(:id,:sid,:data)"), {"id": run_id, "sid": session_id, "data": json.dumps({"id": run_id, "parent_id": session_id, "discussion_scope_id": scope_id})})
        raw.execute(text("""INSERT INTO chat_discussion_scopes(scope_id,owner_id,session_id,run_id,mode,forum_ids,tids,pids,created_at)
            VALUES(:scope,'local',:session,:run,'selected',:forums,:tids,'[]',1)"""), {"scope": scope_id, "session": session_id, "run": run_id, "forums": json.dumps([forum_id]), "tids": json.dumps([tid])})

    settings = SimpleNamespace(db_path=db_path, export_default_strategy="cache_only")
    monkeypatch.setattr("yamibo_mcp.application.assistant_operation_plans.load_settings", lambda: settings)
    monkeypatch.setattr(
        "yamibo_mcp.application.assistant_operation_plans.connect",
        lambda *_a, **_k: DatabaseConnection(pg_engine.connect(), backend="postgres"),
    )
    original_insert = AssistantOperationPlansRepository.insert

    def fail_after_parent_insert(repo, plan):
        original_insert(repo, plan)
        raise RuntimeError("injected operation item write failure")

    monkeypatch.setattr(AssistantOperationPlansRepository, "insert", fail_after_parent_insert)
    with pytest.raises(RuntimeError, match="injected operation item write failure"):
        create_operation_plan(
            session_id=session_id, run_id=run_id, request_key="failed", action="export",
            tids=[tid], require_images=True, export_strategy="cache_only",
        )
    with pg_engine.connect() as raw:
        assert raw.execute(text("SELECT count(*) FROM assistant_operation_plans WHERE session_id=:sid"), {"sid": session_id}).scalar_one() == 0
        assert raw.execute(text("SELECT count(*) FROM assistant_operation_items WHERE tid=:tid"), {"tid": tid}).scalar_one() == 0
    monkeypatch.setattr(AssistantOperationPlansRepository, "insert", original_insert)

    plan = create_operation_plan(
        session_id=session_id, run_id=run_id, request_key="approved", action="export",
        tids=[tid], require_images=True, export_strategy="cache_only",
    )
    assert [step["kind"] for step in plan["items"][0]["steps"]] == ["archive", "export"]
    listed = list_operation_plans(session_id=session_id, limit=1, offset=0)
    assert listed["has_more"] is False
    assert [item["plan_id"] for item in listed["plans"]] == [plan["plan_id"]]
    assert list_operation_plans(session_id=other_session_id)["plans"] == []

    # A deleted conversation revokes any still-pending authorization handoff.
    with pg_engine.begin() as raw:
        raw.execute(
            text("UPDATE chat_sessions SET data=:data WHERE id=:sid"),
            {"data": json.dumps({"id": session_id, "owner_id": "local", "deleted": True}), "sid": session_id},
        )
    with pytest.raises(OperationPlanError, match="Session is unavailable") as exc_info:
        approve_operation_plan(
            plan_id=plan["plan_id"], session_id=session_id,
            plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
        )
    assert exc_info.value.code == "SESSION_NOT_FOUND"
    with pg_engine.connect() as raw:
        assert raw.execute(
            text("SELECT status FROM assistant_operation_plans WHERE plan_id=:pid"),
            {"pid": plan["plan_id"]},
        ).scalar_one() == "awaiting_approval"
        assert raw.execute(text("SELECT count(*) FROM jobs WHERE tid=:tid"), {"tid": tid}).scalar_one() == 0
    with pg_engine.begin() as raw:
        raw.execute(
            text("UPDATE chat_sessions SET data=:data WHERE id=:sid"),
            {"data": json.dumps({"id": session_id, "owner_id": "local"}), "sid": session_id},
        )

    def approve_once(_):
        return approve_operation_plan(
            plan_id=plan["plan_id"], session_id=session_id,
            plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve_once, range(2)))
    assert [item["status"] for item in results] == ["approved_pending_execution"] * 2
    assert [item["execution"] for item in results] == ["not_started"] * 2

    with pg_engine.begin() as raw:
        raw.execute(text("DELETE FROM assistant_operation_plans WHERE session_id=:sid"), {"sid": session_id})
        raw.execute(text("DELETE FROM chat_runs WHERE id=:run"), {"run": run_id})
        raw.execute(text("DELETE FROM chat_discussion_scopes WHERE scope_id=:scope"), {"scope": scope_id})
        raw.execute(text("DELETE FROM chat_sessions WHERE id=:sid"), {"sid": session_id})
        raw.execute(text("DELETE FROM chat_sessions WHERE id=:sid"), {"sid": other_session_id})
        raw.execute(text("DELETE FROM threads WHERE tid=:tid"), {"tid": tid})
        raw.execute(text("DELETE FROM forums WHERE forum_id=:fid"), {"fid": forum_id})
