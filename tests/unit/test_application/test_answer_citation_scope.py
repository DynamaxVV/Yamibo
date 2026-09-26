"""Citation publication rechecks scope before loading an unchanged source body."""
from unittest.mock import patch

import pytest

from yamibo_mcp.application.assistant_evidence_queries import read_discussion_source, validate_source_receipts
from yamibo_mcp.db.repositories.assistant_evidence import AssistantEvidenceRepository
from tests.unit.test_application.test_assistant_evidence_queries import (
    _call_with_db, _scope, _seed_run, _seed_thread,
)


def _receipt(db):
    _seed_run(db)
    _seed_thread(db)
    frozen = _scope(db, start_at="2026-09-25", end_at="2026-09-25")
    source = _call_with_db(db, read_discussion_source, run_id="run-1",
                           scope_id=frozen.data["scope_id"], tid=70001, pid=80001)
    assert source.ok
    return source.data["receipt_id"]


@pytest.mark.parametrize("sql, parameters", [
    ("UPDATE threads SET forum_id = ? WHERE tid = 70001", (2,)),
    ("UPDATE threads SET content_kind = ? WHERE tid = 70001", ("novel",)),
    ("UPDATE threads SET sync_time = ? WHERE tid = 70001", ("2099-01-01",)),
    ("UPDATE floors SET pub_time = ? WHERE pid = 80001", ("2026-09-24T23:59:59+00:00",)),
    ("UPDATE floors SET pub_time = ? WHERE pid = 80001", ("2026-09-26T00:00:00+00:00",)),
    ("UPDATE floors SET pub_time = NULL WHERE pid = 80001", ()),
    ("UPDATE forums SET enabled = 0 WHERE forum_id = 5", ()),
    ("UPDATE chat_discussion_scopes SET tids = ? WHERE run_id = 'run-1'", ("[70002]",)),
    ("UPDATE chat_discussion_scopes SET pids = ? WHERE run_id = 'run-1'", ("[80002]",)),
])
def test_scope_drift_rejected_without_loading_body(db, sql, parameters):
    receipt = _receipt(db)
    db.execute(sql, parameters)
    reads = []
    original = AssistantEvidenceRepository.read_floor

    def observed(self, *args, **kwargs):
        reads.append(kwargs)
        return original(self, *args, **kwargs)

    with patch.object(AssistantEvidenceRepository, "read_floor", observed):
        result = _call_with_db(db, validate_source_receipts, run_id="run-1", receipt_ids=[receipt])
    assert not result.ok
    assert result.error.code == "SOURCE_CHANGED"
    assert result.data["sources"] == []
    assert all(call.get("metadata_only") for call in reads)


@pytest.mark.parametrize("table,column,value", [
    ("chat_source_receipts", "owner_id", "another-owner"),
    ("chat_source_receipts", "session_id", "another-session"),
    ("chat_source_receipts", "run_id", "another-run"),
    ("chat_discussion_scopes", "owner_id", "another-owner"),
    ("chat_discussion_scopes", "session_id", "another-session"),
    ("chat_discussion_scopes", "run_id", "another-run"),
])
def test_ownership_drift_rejected_before_source_read(db, table, column, value):
    receipt = _receipt(db)
    db.execute(f"UPDATE {table} SET {column} = ?", (value,))
    with patch.object(AssistantEvidenceRepository, "read_floor") as read:
        result = _call_with_db(db, validate_source_receipts, run_id="run-1", receipt_ids=[receipt])
    assert not result.ok
    assert result.error.code == "RECEIPT_NOT_READ"
    assert result.data["sources"] == []
    read.assert_not_called()


def test_metadata_race_uses_conditional_body_read(db):
    receipt = _receipt(db)
    original = AssistantEvidenceRepository.read_floor
    reads = []

    def move_between_reads(self, *args, **kwargs):
        reads.append(kwargs)
        if kwargs.get("expected_metadata"):
            self.conn.execute("UPDATE floors SET pub_time = '2026-09-24' WHERE pid = 80001")
        return original(self, *args, **kwargs)

    with patch.object(AssistantEvidenceRepository, "read_floor", move_between_reads):
        result = _call_with_db(db, validate_source_receipts, run_id="run-1", receipt_ids=[receipt])
    assert not result.ok
    assert result.error.code == "SOURCE_CHANGED"
    assert result.data["sources"] == []
    assert reads[0] == {"metadata_only": True}
    assert reads[1]["expected_metadata"]["forum_id"] == 5
