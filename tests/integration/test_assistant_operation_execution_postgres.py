from __future__ import annotations

import json
import os
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import yamibo_mcp.application.assistant_operation_execution as operation_execution
from yamibo_mcp.application.assistant_operation_execution import advance_operation_plans
from yamibo_mcp.application.assistant_operation_plans import approve_operation_plan, create_operation_plan
from yamibo_mcp.db.connection import DatabaseConnection, migrate
from yamibo_mcp.db.repositories.assistant_operation_plans import AssistantOperationPlansRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository


def test_operation_execution_enqueue_recovery_and_export_validation_are_atomic(pg_engine, monkeypatch, tmp_path):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    suffix = uuid.uuid4().hex[:10]
    tid = 960_000_000 + int(suffix, 16) % 10_000_000
    forum_id = 398_000 + int(suffix, 16) % 10_000
    session_id, run_id, scope_id = f"exec-session-{suffix}", f"exec-run-{suffix}", f"exec-scope-{suffix}"
    with pg_engine.connect() as raw:
        migrate(DatabaseConnection(raw, backend="postgres"), schema="public")
    with pg_engine.begin() as raw:
        raw.execute(text("INSERT INTO forums(forum_id,name,content_kind,base_url,enabled) VALUES(:fid,'operation execution test','discussion','https://example.invalid',TRUE)"), {"fid": forum_id})
        raw.execute(text("""INSERT INTO threads(tid,page_type,raw_title,pub_time,sync_time,last_pid,archive_status,capture_mode,validation_status,forum_id,content_kind)
            VALUES(:tid,'discussion','operation execution test','2026-09-25 08:00:00+00','2026-09-25 00:00:00+00',:pid,'complete','text_only','valid',:fid,'discussion')"""), {"tid": tid, "pid": tid + 1, "fid": forum_id})
        raw.execute(text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"), {"id": session_id, "data": json.dumps({"id": session_id, "owner_id": "local"})})
        raw.execute(text("INSERT INTO chat_runs(id,parent_id,data) VALUES(:id,:sid,:data)"), {"id": run_id, "sid": session_id, "data": json.dumps({"id": run_id, "parent_id": session_id, "discussion_scope_id": scope_id})})
        raw.execute(text("""INSERT INTO chat_discussion_scopes(scope_id,owner_id,session_id,run_id,mode,forum_ids,tids,pids,created_at)
            VALUES(:scope,'local',:session,:run,'selected',:forums,:tids,'[]',1)"""), {"scope": scope_id, "session": session_id, "run": run_id, "forums": json.dumps([forum_id]), "tids": json.dumps([tid])})

    settings = SimpleNamespace(db_path="isolated-execution-test", export_default_strategy="cache_only", data_dir=tmp_path)
    monkeypatch.setattr("yamibo_mcp.application.assistant_operation_plans.load_settings", lambda: settings)
    monkeypatch.setattr(
        "yamibo_mcp.application.assistant_operation_plans.connect",
        lambda *_a, **_k: DatabaseConnection(pg_engine.connect(), backend="postgres"),
    )
    plan = create_operation_plan(
        session_id=session_id, run_id=run_id, request_key="approved", action="export",
        tids=[tid], require_images=True, export_strategy="cache_only",
    )
    approve_operation_plan(
        plan_id=plan["plan_id"], session_id=session_id,
        plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
    )

    def temporary_enqueue_failure(*_args, **_kwargs):
        raise RuntimeError("temporary remote pause")

    original_enqueue = operation_execution.archive_thread_job
    monkeypatch.setattr(operation_execution, "archive_thread_job", temporary_enqueue_failure)
    retry_conn = DatabaseConnection(pg_engine.connect(), backend="postgres")
    with pytest.raises(RuntimeError, match="temporary remote pause"):
        advance_operation_plans(retry_conn, settings)
    retry_conn.close()
    with pg_engine.connect() as raw:
        assert raw.execute(text("SELECT status FROM assistant_operation_plans WHERE plan_id=:pid"), {"pid": plan["plan_id"]}).scalar_one() == "approved_pending_execution"
        assert raw.execute(text("SELECT count(*) FROM assistant_operation_phases WHERE plan_id=:pid"), {"pid": plan["plan_id"]}).scalar_one() == 0
    monkeypatch.setattr(operation_execution, "archive_thread_job", original_enqueue)

    # Failure after physical enqueue but before the receipt must roll both back.
    original_put_phase = AssistantOperationPlansRepository.put_phase

    def fail_before_receipt(repo, **kwargs):
        original_put_phase(repo, **kwargs)
        raise RuntimeError("injected receipt failure")

    monkeypatch.setattr(AssistantOperationPlansRepository, "put_phase", fail_before_receipt)
    failed_conn = DatabaseConnection(pg_engine.connect(), backend="postgres")
    with pytest.raises(RuntimeError, match="injected receipt failure"):
        advance_operation_plans(failed_conn, settings)
    failed_conn.close()
    with pg_engine.connect() as raw:
        assert raw.execute(text("SELECT count(*) FROM jobs WHERE tid=:tid"), {"tid": tid}).scalar_one() == 0
        assert raw.execute(text("SELECT count(*) FROM assistant_operation_phases WHERE plan_id=:pid"), {"pid": plan["plan_id"]}).scalar_one() == 0
    monkeypatch.setattr(AssistantOperationPlansRepository, "put_phase", original_put_phase)

    def advance_once(_):
        conn = DatabaseConnection(pg_engine.connect(), backend="postgres")
        try:
            return advance_operation_plans(conn, settings)
        finally:
            conn.close()

    # Competing daemon ticks serialize on the plan row and create one phase/Job.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(advance_once, range(2)))
    with pg_engine.connect() as raw:
        jobs = raw.execute(text("SELECT job_id, job_type FROM jobs WHERE tid=:tid"), {"tid": tid}).fetchall()
        phases = raw.execute(text("SELECT phase_key, job_id, state FROM assistant_operation_phases WHERE plan_id=:pid"), {"pid": plan["plan_id"]}).fetchall()
    assert len(jobs) == len(phases) == 1
    archive_job_id = phases[0][1]
    assert phases[0][2] == "queued"

    # A restarted scheduler observes the real terminal Job and the full archive state, then queues export.
    with pg_engine.begin() as raw:
        raw.execute(text("UPDATE threads SET archive_status='complete', capture_mode='full' WHERE tid=:tid"), {"tid": tid})
        raw.execute(text("UPDATE jobs SET status='succeeded', artifacts_json=:artifacts, finished_at=now() WHERE job_id=:jid"), {"artifacts": "{}", "jid": archive_job_id})
    advance_once(0)
    with pg_engine.connect() as raw:
        item = raw.execute(text("SELECT item_json FROM assistant_operation_items WHERE plan_id=:pid AND tid=:tid"), {"pid": plan["plan_id"], "tid": tid}).scalar_one()
        export_phase = raw.execute(text("SELECT job_id, state FROM assistant_operation_phases WHERE plan_id=:pid AND step_index=1"), {"pid": plan["plan_id"]}).one()
    assert json.loads(item)["steps"][0]["state"] == "succeeded"
    assert export_phase[1] == "queued"

    export_path = tmp_path / "exports" / f"thread_{tid}.zip"
    export_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(export_path, "w") as archive:
        archive.writestr("thread.txt", "valid export")
    with pg_engine.begin() as raw:
        raw.execute(text("UPDATE jobs SET status='succeeded', artifacts_json=:artifacts, finished_at=now() WHERE job_id=:jid"), {"artifacts": json.dumps({"export_path": str(export_path)}), "jid": export_phase[0]})
    advance_once(0)
    with pg_engine.connect() as raw:
        status = raw.execute(text("SELECT status FROM assistant_operation_plans WHERE plan_id=:pid"), {"pid": plan["plan_id"]}).scalar_one()
        final_item = json.loads(raw.execute(text("SELECT item_json FROM assistant_operation_items WHERE plan_id=:pid AND tid=:tid"), {"pid": plan["plan_id"], "tid": tid}).scalar_one())
    assert status == "completed"
    assert final_item["steps"][1]["state"] == "succeeded"
    assert Path(final_item["steps"][1]["export_path"]) == export_path

    with pg_engine.begin() as raw:
        raw.execute(text("DELETE FROM assistant_operation_plans WHERE plan_id=:pid"), {"pid": plan["plan_id"]})
        raw.execute(text("DELETE FROM chat_runs WHERE id=:run"), {"run": run_id})
        raw.execute(text("DELETE FROM chat_discussion_scopes WHERE scope_id=:scope"), {"scope": scope_id})
        raw.execute(text("DELETE FROM chat_sessions WHERE id=:sid"), {"sid": session_id})
        raw.execute(text("DELETE FROM threads WHERE tid=:tid"), {"tid": tid})
        raw.execute(text("DELETE FROM forums WHERE forum_id=:fid"), {"fid": forum_id})
