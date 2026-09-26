from __future__ import annotations

import json
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from yamibo_mcp.application.assistant_evidence_queries import (
    freeze_discussion_scope,
    list_run_source_receipts,
    read_discussion_source,
    validate_source_receipts,
)
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate


def test_postgres_frozen_scope_and_receipts_are_run_bound_and_content_checked(pg_engine):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    suffix = uuid.uuid4().hex[:8]
    tid, pid, forum_id = 980_000_000 + int(suffix, 16) % 10_000_000, 990_000_000 + int(suffix, 16) % 10_000_000, 399_000 + int(suffix, 16) % 10_000
    session_id, run_id = f"evidence-session-{suffix}", f"evidence-run-{suffix}"
    with pg_engine.connect() as raw:
        migrate(DatabaseConnection(raw, backend="postgres"), schema="public")
    with pg_engine.begin() as raw:
        raw.execute(text("INSERT INTO forums(forum_id,name,content_kind,base_url,enabled) VALUES(:fid,'evidence test','discussion','https://example.invalid',TRUE) ON CONFLICT(forum_id) DO UPDATE SET content_kind='discussion',enabled=TRUE"), {"fid": forum_id})
        raw.execute(text("INSERT INTO threads(tid,page_type,raw_title,pub_time,sync_time,last_pid,archive_status,validation_status,forum_id,content_kind) VALUES(:tid,'discussion','evidence','2026-09-25 08:00:00+00','2026-09-25 00:00:00+00',:pid,'complete','valid',:fid,'discussion')"), {"tid": tid, "pid": pid, "fid": forum_id})
        raw.execute(text("INSERT INTO floors(pid,tid,floor_no,content,pub_time,has_images) VALUES(:pid,:tid,1,'PG 第一段\nPG 第二段','2026-09-25 08:00:00+00',FALSE)"), {"pid": pid, "tid": tid})
        raw.execute(text("INSERT INTO chat_sessions(id,parent_id,data) VALUES(:id,'',:data)"), {"id": session_id, "data": json.dumps({"id": session_id, "owner_id": "local"})})
        raw.execute(text("INSERT INTO chat_runs(id,parent_id,data) VALUES(:id,:sid,:data)"), {"id": run_id, "sid": session_id, "data": json.dumps({"id": run_id, "parent_id": session_id})})

    def call(fn, **kwargs):
        settings = MagicMock()
        settings.db_path = ":isolated-test-db:"
        with patch("yamibo_mcp.application.assistant_evidence_queries.load_settings", return_value=settings), \
             patch("yamibo_mcp.application.assistant_evidence_queries.connect", side_effect=lambda *_a, **_k: DatabaseConnection(pg_engine.connect(), backend="postgres")):
            return fn(**kwargs)

    frozen = call(freeze_discussion_scope, run_id=run_id, mode="selected", forum_ids=[forum_id], tids=[tid], pids=[pid])
    assert frozen.ok is True
    read = call(read_discussion_source, scope_id=frozen.data["scope_id"], run_id=run_id, tid=tid, pid=pid)
    assert read.ok is True
    assert read.data["paragraph_range"] == {"start": 1, "end": 2}
    assert read.data["content"] == "PG 第一段\nPG 第二段"
    assert call(validate_source_receipts, run_id=run_id, receipt_ids=[read.data["receipt_id"]]).ok is True
    listed = call(list_run_source_receipts, run_id=run_id)
    assert listed.ok is True
    assert listed.data["items"][0]["source_url"] == f"/threads/{tid}#pid-{pid}"
    assert listed.data["items"][0]["floor_no"] == 1

    with pg_engine.begin() as raw:
        raw.execute(text("UPDATE floors SET content='PG changed' WHERE pid=:pid"), {"pid": pid})
    stale = call(validate_source_receipts, run_id=run_id, receipt_ids=[read.data["receipt_id"]])
    assert stale.ok is False
    assert stale.error.code == "SOURCE_CHANGED"
    stale_listing = call(list_run_source_receipts, run_id=run_id)
    assert stale_listing.data["items"][0]["status"] == "source_changed"
    assert stale_listing.data["items"][0]["source_url"] is None

    with pg_engine.begin() as raw:
        raw.execute(text("DELETE FROM chat_source_receipts WHERE run_id=:run_id"), {"run_id": run_id})
        raw.execute(text("DELETE FROM chat_discussion_scopes WHERE run_id=:run_id"), {"run_id": run_id})
        raw.execute(text("DELETE FROM chat_runs WHERE id=:run_id"), {"run_id": run_id})
        raw.execute(text("DELETE FROM chat_sessions WHERE id=:session_id"), {"session_id": session_id})
        raw.execute(text("DELETE FROM floors WHERE tid=:tid"), {"tid": tid})
        raw.execute(text("DELETE FROM threads WHERE tid=:tid"), {"tid": tid})
        raw.execute(text("DELETE FROM forums WHERE forum_id=:fid"), {"fid": forum_id})
