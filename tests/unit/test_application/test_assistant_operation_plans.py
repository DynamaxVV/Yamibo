from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from yamibo_mcp.application.assistant_operation_plans import (
    OperationPlanError,
    approve_operation_plan,
    create_operation_plan,
    get_operation_plan,
    list_operation_plans,
)
from yamibo_mcp.application.assistant_operation_execution import advance_operation_plans, cancel_operation_plan
from yamibo_mcp.application.assistant_operation_execution import _validate_export
from yamibo_mcp.db.connection import connect as real_connect
from yamibo_mcp.db.repositories.assistant_operation_plans import canonical_hash


def _seed(db, *, capture_mode="text_only", archive_status="complete", scope_tids=(70001,)):
    db.execute(
        "INSERT INTO forums(forum_id,name,content_kind,base_url,enabled) VALUES(5,'test','discussion','https://example.invalid',1) ON CONFLICT(forum_id) DO UPDATE SET content_kind='discussion',enabled=1"
    )
    db.execute(
        """INSERT INTO threads (tid,page_type,raw_title,pub_time,sync_time,last_pid,
                archive_status,capture_mode,validation_status,forum_id,content_kind)
           VALUES (70001,'discussion','讨论计划测试','2026-09-25T00:00:00+00:00',
                '2026-09-25T00:00:00+00:00',80001,?,?, 'valid',5,'discussion')""",
        (archive_status, capture_mode),
    )
    session = {"id": "session-1", "owner_id": "owner-1"}
    run = {"id": "run-1", "parent_id": "session-1", "discussion_scope_id": "scope-1"}
    db.execute("INSERT INTO chat_sessions(id,parent_id,data) VALUES(?,?,?)", ("session-1", "", json.dumps(session)))
    db.execute("INSERT INTO chat_runs(id,parent_id,data) VALUES(?,?,?)", ("run-1", "session-1", json.dumps(run)))
    db.execute(
        """INSERT INTO chat_discussion_scopes(scope_id,owner_id,session_id,run_id,mode,
               forum_ids,tids,pids,start_at,end_at,created_at)
           VALUES('scope-1','owner-1','session-1','run-1','selected','[5]',?,?,NULL,NULL,1)""",
        (json.dumps(list(scope_tids)), "[]"),
    )
    db.commit()


def _call(db, fn, **kwargs):
    db_path = str(db._conn.engine.url.database)
    with patch("yamibo_mcp.application.assistant_operation_plans.load_settings", return_value=SimpleNamespace(db_path=db_path, export_default_strategy="cache_only")), patch(
        "yamibo_mcp.application.assistant_operation_plans.connect",
        side_effect=lambda *_a, **_k: real_connect(db_path, bootstrap=False),
    ):
        return fn(**kwargs)


def _draft(db, **changes):
    args = {
        "session_id": "session-1", "run_id": "run-1", "request_key": "request-1",
        "action": "export", "tids": [70001], "require_images": True,
        "export_strategy": "cache_only",
    }
    args.update(changes)
    return _call(db, create_operation_plan, **args)


def test_export_plan_freezes_required_full_upgrade_and_approval_is_independent(db):
    _seed(db, capture_mode="text_only", archive_status="complete")
    plan = _draft(db)
    assert plan["status"] == "awaiting_approval"
    assert plan["approval_ready"] is True
    assert plan["items"][0]["export_format"] == "zip"
    assert [step["kind"] for step in plan["items"][0]["steps"]] == ["archive", "export"]
    assert plan["items"][0]["steps"][0]["upgrade_from"] == "text_only"
    assert plan["execution"] == "not_started"

    approved = _call(
        db, approve_operation_plan, plan_id=plan["plan_id"], session_id="session-1",
        plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
    )
    replay = _call(
        db, approve_operation_plan, plan_id=plan["plan_id"], session_id="session-1",
        plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
    )
    assert approved["status"] == replay["status"] == "approved_pending_execution"
    assert approved["plan_hash"] == replay["plan_hash"]
    assert approved["execution"] == "not_started"
    assert approved["approval_ready"] is False
    assert _call(db, get_operation_plan, plan_id=plan["plan_id"], session_id="session-1")["status"] == "approved_pending_execution"


def test_plan_idempotency_scope_and_stale_approval_are_enforced(db):
    _seed(db, capture_mode="full", archive_status="complete")
    first = _draft(db)
    repeated = _draft(db)
    assert repeated["plan_id"] == first["plan_id"]
    with pytest.raises(OperationPlanError, match="request_key"):
        _draft(db, tids=[70002])
    with pytest.raises(OperationPlanError, match="version or hash"):
        _call(
            db, approve_operation_plan, plan_id=first["plan_id"], session_id="session-1",
            plan_version=first["plan_version"], plan_hash="0" * 64,
        )
    db.execute(
        "INSERT INTO chat_sessions(id,parent_id,data) VALUES(?,?,?)",
        ("other-session", "", json.dumps({"id": "other-session", "owner_id": "owner-2"})),
    )
    db.commit()
    with pytest.raises(OperationPlanError, match="does not belong"):
        _call(db, get_operation_plan, plan_id=first["plan_id"], session_id="other-session")


def test_legacy_cost_snapshot_remains_approvable(db):
    _seed(db, capture_mode="full", archive_status="complete")
    plan = _draft(db)
    row = db.execute(
        "SELECT plan_json FROM assistant_operation_plans WHERE plan_id=?",
        (plan["plan_id"],),
    ).fetchone()
    snapshot = json.loads(row["plan_json"])
    snapshot.update(cost_limit=10, planned_cost_units=1, cost_model="legacy")
    legacy_hash = canonical_hash(snapshot)
    db.execute(
        "UPDATE assistant_operation_plans SET plan_json=?, plan_hash=? WHERE plan_id=?",
        (json.dumps(snapshot), legacy_hash, plan["plan_id"]),
    )
    db.commit()

    existing = _call(db, get_operation_plan, plan_id=plan["plan_id"], session_id="session-1")
    assert existing["approval_ready"] is True
    assert existing["plan_hash"] == legacy_hash
    assert "cost_limit" not in existing
    approved = _call(
        db, approve_operation_plan, plan_id=plan["plan_id"], session_id="session-1",
        plan_version=plan["plan_version"], plan_hash=legacy_hash,
    )
    assert approved["status"] == "approved_pending_execution"


def test_operation_plan_list_is_session_scoped_and_paginated(db):
    _seed(db, capture_mode="full", archive_status="complete")
    first = _draft(db, action="archive", export_strategy=None, request_key="list-1")
    second = _draft(db, action="archive", export_strategy=None, request_key="list-2")
    db.execute(
        "INSERT INTO chat_sessions(id,parent_id,data) VALUES(?,?,?)",
        ("other-session", "", json.dumps({"id": "other-session", "owner_id": "owner-2"})),
    )
    db.commit()

    page = _call(db, list_operation_plans, session_id="session-1", limit=1, offset=0)
    assert len(page["plans"]) == 1
    assert page["has_more"] is True
    next_page = _call(db, list_operation_plans, session_id="session-1", limit=1, offset=1)
    assert len(next_page["plans"]) == 1
    assert next_page["has_more"] is False
    assert page["plans"][0]["plan_id"] != next_page["plans"][0]["plan_id"]
    assert _call(db, list_operation_plans, session_id="other-session")["plans"] == []
    with pytest.raises(OperationPlanError, match="between 1 and 100"):
        _call(db, list_operation_plans, session_id="session-1", limit=101)
    with pytest.raises(OperationPlanError, match="between 0 and 10000"):
        _call(db, list_operation_plans, session_id="session-1", offset=10_001)
    assert first["plan_id"] != second["plan_id"]


def test_plan_preserves_tool_scope_for_non_discussion_targets(db):
    _seed(db, capture_mode="text_only", archive_status="complete")
    plan = _draft(db)
    assert [step["kind"] for step in plan["items"][0]["steps"]] == ["archive", "export"]
    assert "cost_limit" not in plan
    db.execute("UPDATE threads SET content_kind='comic' WHERE tid=70001")
    db.commit()
    with pytest.raises(OperationPlanError, match="not classified as a discussion"):
        _draft(db, request_key="request-2")


def test_discovery_or_date_scope_cannot_be_widened_into_an_operation(db):
    _seed(db, capture_mode="full", archive_status="complete")
    db.execute(
        "UPDATE chat_discussion_scopes SET mode='discovery', tids='[]', start_at='2030-01-01T00:00:00+00:00' WHERE scope_id='scope-1'"
    )
    db.commit()
    with pytest.raises(OperationPlanError, match="exact TIDs"):
        _draft(db, request_key="date-bypass")


def test_approval_rechecks_persisted_item_snapshot_integrity(db):
    _seed(db, capture_mode="full", archive_status="complete")
    plan = _draft(db, action="archive", export_strategy=None)
    db.execute(
        "UPDATE assistant_operation_items SET item_json='{}' WHERE plan_id=? AND tid=?",
        (plan["plan_id"], 70001),
    )
    db.commit()
    with pytest.raises(OperationPlanError, match="Stored plan steps"):
        _call(
            db, approve_operation_plan, plan_id=plan["plan_id"], session_id="session-1",
            plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
        )


def test_plan_insert_failure_rolls_back_parent_and_item_rows(db):
    _seed(db, capture_mode="full", archive_status="complete")
    db.execute(
        """CREATE TRIGGER fail_operation_item BEFORE INSERT ON assistant_operation_items
           BEGIN SELECT RAISE(ABORT, 'injected item write failure'); END"""
    )
    db.commit()
    with pytest.raises(Exception, match="injected item write failure"):
        _draft(db, action="archive", export_strategy=None)
    assert db.execute("SELECT COUNT(*) AS n FROM assistant_operation_plans").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM assistant_operation_items").fetchone()["n"] == 0


def test_cancel_before_scheduler_marks_only_unstarted_steps_cancelled(db):
    _seed(db, capture_mode="text_only", archive_status="complete")
    plan = _draft(db)
    approved = _call(
        db, approve_operation_plan, plan_id=plan["plan_id"], session_id="session-1",
        plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
    )
    db_path = str(db._conn.engine.url.database)
    with patch("yamibo_mcp.application.assistant_operation_execution.load_settings", return_value=SimpleNamespace(db_path=db_path)), patch(
        "yamibo_mcp.application.assistant_operation_execution.connect",
        side_effect=lambda *_a, **_k: real_connect(db_path, bootstrap=False),
    ):
        cancelled = cancel_operation_plan(plan_id=plan["plan_id"], session_id="session-1")
    assert approved["status"] == "approved_pending_execution"
    assert cancelled["status"] == "cancellation_requested"
    advance_operation_plans(db, SimpleNamespace(data_dir="/tmp/yamibo-test-data", export_default_strategy="cache_only"))
    completed_cancellation = _call(db, get_operation_plan, plan_id=plan["plan_id"], session_id="session-1")
    assert completed_cancellation["status"] == "cancelled"
    assert [step["state"] for step in completed_cancellation["items"][0]["steps"]] == ["cancelled", "cancelled"]
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_export_validation_rejects_missing_outside_and_corrupt_zip(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    missing = _validate_export(root, {"export_path": str(root / "missing.zip")}, "zip")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"not a zip")
    escaped = _validate_export(root, {"export_path": str(outside)}, "zip")
    corrupt = root / "corrupt.zip"
    corrupt.write_bytes(b"PK\x03\x04invalid")
    bad_zip = _validate_export(root, {"export_path": str(corrupt)}, "zip")
    assert missing[0] is False and "missing" in missing[2]
    assert escaped[0] is False and "outside" in escaped[2]
    assert bad_zip[0] is False and "cannot be opened" in bad_zip[2]


def test_one_post_failure_keeps_other_post_progress_and_reports_partial_completion(db, tmp_path):
    _seed(db, capture_mode="text_only", archive_status="complete", scope_tids=(70001, 70002))
    db.execute(
        """INSERT INTO threads(tid,page_type,raw_title,pub_time,sync_time,last_pid,archive_status,
                  capture_mode,validation_status,forum_id,content_kind)
           VALUES(70002,'discussion','第二个讨论','2026-09-25T00:00:00+00:00',
                  '2026-09-25T00:00:00+00:00',80002,'complete','text_only','valid',5,'discussion')"""
    )
    db.commit()
    plan = _draft(db, tids=[70001, 70002])
    _call(
        db, approve_operation_plan, plan_id=plan["plan_id"], session_id="session-1",
        plan_version=plan["plan_version"], plan_hash=plan["plan_hash"],
    )
    settings = SimpleNamespace(data_dir=tmp_path, export_default_strategy="cache_only")
    advance_operation_plans(db, settings)
    archive_phases = db.execute(
        "SELECT tid, job_id FROM assistant_operation_phases WHERE plan_id=? AND step_index=0 ORDER BY tid",
        (plan["plan_id"],),
    ).fetchall()
    assert len(archive_phases) == 2
    db.commit()

    db.execute(
        "UPDATE jobs SET status='failed', error_code='REMOTE_FAILED', error_message='failed post' WHERE job_id=?",
        (archive_phases[0]["job_id"],),
    )
    db.execute(
        "UPDATE jobs SET status='succeeded', artifacts_json='{}' WHERE job_id=?",
        (archive_phases[1]["job_id"],),
    )
    db.execute("UPDATE threads SET capture_mode='full', archive_status='complete' WHERE tid=70002")
    db.commit()
    advance_operation_plans(db, settings)
    items = {
        item["tid"]: item for item in _call(db, get_operation_plan, plan_id=plan["plan_id"], session_id="session-1")["items"]
    }
    assert items[70001]["stage"] == "failed"
    assert items[70002]["steps"][0]["state"] == "succeeded"
    assert items[70002]["steps"][1]["state"] == "queued"
    export_job_id = items[70002]["steps"][1]["job_id"]

    db.execute(
        "UPDATE jobs SET status='failed', error_code='EXPORT_FAILED', error_message='export failed' WHERE job_id=?",
        (export_job_id,),
    )
    db.commit()
    advance_operation_plans(db, settings)
    final = _call(db, get_operation_plan, plan_id=plan["plan_id"], session_id="session-1")
    assert final["status"] == "partially_completed"
    assert final["items"][0]["stage"] == "failed"
    assert final["items"][1]["steps"][0]["state"] == "succeeded"
    assert final["items"][1]["steps"][1]["state"] == "failed"
