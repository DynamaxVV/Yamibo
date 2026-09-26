from __future__ import annotations

import json
import hashlib
from unittest.mock import MagicMock, patch

from yamibo_mcp.application.assistant_evidence_queries import (
    freeze_discussion_scope,
    list_run_source_receipts,
    read_discussion_source,
    validate_source_receipts,
)
from yamibo_mcp.domain.thread_fingerprint import floor_content_hash
from yamibo_mcp.db.connection import connect as real_connect


def _seed_run(db, *, run_id="run-1", session_id="session-1", owner_id="owner-1"):
    db.execute(
        "INSERT INTO chat_sessions(id,parent_id,data) VALUES(?,?,?)",
        (session_id, "", json.dumps({"id": session_id, "owner_id": owner_id})),
    )
    db.execute(
        "INSERT INTO chat_runs(id,parent_id,data) VALUES(?,?,?)",
        (run_id, session_id, json.dumps({"id": run_id, "parent_id": session_id, "status": "running"})),
    )


def _seed_thread(db, *, tid=70001, pid=80001, content="第一段\n第二段", pub_time="2026-09-25T08:00:00+00:00", sync_time="2026-09-25T00:00:00+00:00"):
    db.execute(
        """INSERT INTO threads (tid, page_type, raw_title, pub_time, sync_time,
                                last_pid, archive_status, validation_status, forum_id, content_kind)
           VALUES (?, 'discussion', '证据测试', ?, ?, ?, 'complete', 'valid', 5, 'discussion')""",
        (tid, pub_time, sync_time, pid),
    )
    db.execute(
        """INSERT INTO floors (pid, tid, floor_no, content, pub_time, has_images, content_hash)
           VALUES (?, ?, 1, ?, ?, 0, ?)""",
        (pid, tid, content, pub_time, floor_content_hash(content)),
    )


def _call_with_db(db, function, **kwargs):
    settings = MagicMock()
    settings.db_path = ":isolated-test-db:"
    db.commit()
    db_path = str(db._conn.engine.url.database)
    with patch("yamibo_mcp.application.assistant_evidence_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.assistant_evidence_queries.connect", side_effect=lambda *_args, **_kwargs: real_connect(db_path, bootstrap=False)):
        return function(**kwargs)


def _scope(db, *, run_id="run-1", **kwargs):
    return _call_with_db(
        db,
        freeze_discussion_scope,
        run_id=run_id,
        mode="selected",
        forum_ids=[5],
        tids=[70001],
        pids=[80001],
        **kwargs,
    )


def test_freeze_read_and_run_bound_receipt_validation(db):
    _seed_run(db)
    _seed_run(db, run_id="run-2", session_id="session-2", owner_id="owner-1")
    _seed_thread(db)
    frozen = _scope(db, start_at="2026-09-25", end_at="2026-09-25")

    assert frozen.ok is True
    read = _call_with_db(
        db,
        read_discussion_source,
        scope_id=frozen.data["scope_id"],
        run_id="run-1",
        tid=70001,
        pid=80001,
        paragraph_start=2,
    )

    assert read.ok is True
    assert read.data["content"] == "第二段"
    assert read.data["paragraph_range"] == {"start": 2, "end": 2}
    assert read.data["content_hash"] == "sha256:" + hashlib.sha256("第一段\n第二段".encode()).hexdigest()
    listed = _call_with_db(db, list_run_source_receipts, run_id="run-1")
    assert listed.ok is True
    assert listed.data["items"][0]["source_url"] == "/threads/70001#pid-80001"
    assert listed.data["items"][0]["floor_no"] == 1
    valid = _call_with_db(
        db,
        validate_source_receipts,
        run_id="run-1",
        receipt_ids=[read.data["receipt_id"]],
    )
    forged_for_other_run = _call_with_db(
        db,
        validate_source_receipts,
        run_id="run-2",
        receipt_ids=[read.data["receipt_id"]],
    )
    assert valid.ok is True and valid.data["valid"] is True
    assert forged_for_other_run.ok is False
    assert forged_for_other_run.error.code == "RECEIPT_NOT_READ"
    # Even normalization-equivalent edits invalidate a receipt: it fingerprints
    # the exact source bytes the assistant received.
    db.execute("UPDATE floors SET content = ? WHERE pid = ?", ("第一段 \n第二段", 80001))
    db.commit()
    stale = _call_with_db(
        db,
        validate_source_receipts,
        run_id="run-1",
        receipt_ids=[read.data["receipt_id"]],
    )
    assert stale.ok is False
    assert stale.error.code == "SOURCE_CHANGED"
    assert stale.data["changed_receipt_ids"] == [read.data["receipt_id"]]
    stale_listing = _call_with_db(db, list_run_source_receipts, run_id="run-1")
    assert stale_listing.data["items"][0]["status"] == "source_changed"
    assert stale_listing.data["items"][0]["source_url"] is None
    hidden_listing = _call_with_db(db, list_run_source_receipts, run_id="run-2")
    assert hidden_listing.data["items"] == []
    db.execute(
        "UPDATE chat_sessions SET data = ? WHERE id = ?",
        (json.dumps({"id": "session-1", "owner_id": "different-owner"}), "session-1"),
    )
    owner_mismatch = _call_with_db(db, list_run_source_receipts, run_id="run-1")
    assert owner_mismatch.error.code == "RECEIPT_ACCESS_DENIED"
    db.execute(
        "UPDATE chat_sessions SET data = ? WHERE id = ?",
        (json.dumps({"id": "session-1", "owner_id": "owner-1"}), "session-1"),
    )


def test_receipt_listing_rejects_missing_run_and_invalid_bounds(db):
    missing = _call_with_db(db, list_run_source_receipts, run_id="missing")
    too_many = _call_with_db(db, list_run_source_receipts, run_id="missing", limit=501)
    assert missing.error.code == "RUN_NOT_FOUND"
    assert too_many.error.code == "INVALID_ARGUMENT"


def test_read_intersects_frozen_tid_pid_forum_and_date(db):
    _seed_run(db)
    _seed_thread(db)
    frozen = _scope(db, start_at="2026-09-26")
    assert frozen.ok is True

    out_of_date = _call_with_db(
        db, read_discussion_source,
        scope_id=frozen.data["scope_id"], run_id="run-1", tid=70001, pid=80001,
    )
    wrong_pid = _call_with_db(
        db, read_discussion_source,
        scope_id=frozen.data["scope_id"], run_id="run-1", tid=70001, pid=80002,
    )
    wrong_tid = _call_with_db(
        db, read_discussion_source,
        scope_id=frozen.data["scope_id"], run_id="run-1", tid=70002, pid=80001,
    )
    assert out_of_date.error.code == "OUT_OF_SCOPE"
    assert wrong_pid.error.code == "OUT_OF_SCOPE"
    assert wrong_tid.error.code == "OUT_OF_SCOPE"


def test_scope_is_idempotent_but_cannot_be_expanded(db):
    _seed_run(db)
    _seed_thread(db)
    first = _scope(db)
    repeated = _scope(db)
    expanded = _call_with_db(
        db, freeze_discussion_scope,
        run_id="run-1", mode="selected", forum_ids=[5], tids=[70001], pids=[],
    )
    assert repeated.data["scope_id"] == first.data["scope_id"]
    assert expanded.error.code == "SCOPE_CONFLICT"


def test_source_updated_after_scope_is_rejected_and_utf8_budget_is_safe(db):
    _seed_run(db)
    _seed_thread(db)
    frozen = _scope(db)
    db.execute(
        "INSERT INTO floors (pid, tid, floor_no, content, pub_time, has_images) VALUES (?, ?, 2, ?, ?, 0)",
        (80002, 70001, "追加的旧时间楼层", "2020-01-01T00:00:00+00:00"),
    )
    db.execute("UPDATE threads SET last_pid = ?, sync_time = ? WHERE tid = ?", (80002, "2099-01-01T00:00:00+00:00", 70001))
    changed = _call_with_db(
        db, read_discussion_source,
        scope_id=frozen.data["scope_id"], run_id="run-1", tid=70001, pid=80001,
    )
    assert changed.error.code == "SOURCE_CHANGED"

    db.execute("UPDATE threads SET sync_time = ? WHERE tid = ?", ("2026-09-25T00:00:00+00:00", 70001))
    bounded = _call_with_db(
        db, read_discussion_source,
        scope_id=frozen.data["scope_id"], run_id="run-1", tid=70001, pid=80001,
        max_bytes=4,
    )
    assert bounded.ok is True
    assert bounded.data["truncated"] is True
    assert bounded.data["content"].encode("utf-8").decode("utf-8") == bounded.data["content"]
    assert len(bounded.data["content"].encode("utf-8")) <= 4
    assert bounded.data["paragraph_range"]["end"] == 0
    assert bounded.data["partial_paragraph"] == 1


def test_missing_floor_time_is_allowed_without_date_scope(db):
    _seed_run(db)
    _seed_thread(db, pub_time=None)
    frozen = _scope(db)
    result = _call_with_db(
        db, read_discussion_source,
        scope_id=frozen.data["scope_id"], run_id="run-1", tid=70001, pid=80001,
    )
    assert result.ok is True


def test_frozen_pid_must_belong_to_a_selected_tid(db):
    _seed_run(db)
    _seed_thread(db, pid=80002)
    result = _call_with_db(
        db, freeze_discussion_scope,
        run_id="run-1", mode="selected", forum_ids=[5], tids=[70001], pids=[99999],
    )
    assert result.error.code == "INVALID_PID_SCOPE"


def test_scope_does_not_accept_non_discussion_or_unknown_forums(db):
    _seed_run(db)
    _seed_thread(db, tid=70002, pid=80002)
    db.execute("UPDATE threads SET content_kind = 'comic' WHERE tid = ?", (70002,))
    result = _call_with_db(
        db, freeze_discussion_scope,
        run_id="run-1", mode="selected", forum_ids=[5], tids=[70002],
    )
    assert result.error.code == "INVALID_THREAD_SCOPE"


def test_out_of_scope_date_does_not_load_source_body(db, monkeypatch):
    from yamibo_mcp.db.repositories.assistant_evidence import AssistantEvidenceRepository

    _seed_run(db)
    _seed_thread(db)
    frozen = _scope(db, start_at="2026-09-26")
    original = AssistantEvidenceRepository.read_floor
    reads = []

    def read(self, tid, pid, **kwargs):
        reads.append(kwargs)
        assert kwargs.get("metadata_only") is True, "out-of-scope source body was loaded"
        return original(self, tid, pid, **kwargs)

    monkeypatch.setattr(AssistantEvidenceRepository, "read_floor", read)
    result = _call_with_db(db, read_discussion_source,
                           scope_id=frozen.data["scope_id"], run_id="run-1", tid=70001, pid=80001)
    assert result.error.code == "OUT_OF_SCOPE"
    assert reads == [{"metadata_only": True}]


def test_source_metadata_change_between_checks_never_loads_body(db):
    from yamibo_mcp.db.repositories.assistant_evidence import AssistantEvidenceRepository

    _seed_thread(db)
    repo = AssistantEvidenceRepository(db)
    metadata = repo.read_floor(70001, 80001, metadata_only=True)
    assert "content" not in metadata
    db.execute("UPDATE floors SET pub_time = ? WHERE pid = ?", ("2099-01-01", 80001))
    assert repo.read_floor(70001, 80001, expected_metadata=metadata) is None
