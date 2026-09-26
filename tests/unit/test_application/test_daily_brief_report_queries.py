import json
from datetime import date, datetime, timezone

import pytest

from yamibo_mcp.application import daily_brief_report_queries as queries


class _Row(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class _Conn:
    backend = "postgres"

    def __init__(self, owner="owner"):
        self.owner = owner
        self.closed = False

    def execute(self, _query, _params):
        return type("Result", (), {"fetchone": lambda _self: _Row(data=json.dumps({"owner_id": self.owner}))})()

    def close(self):
        self.closed = True


def _revision(owner="owner"):
    return {
        "revision_id": "c01b2a3e-0aef-4a68-b2a4-3e037b9b3f93",
        "issue_id": "issue-1",
        "owner_id": owner,
        "source_kind": "manual",
        "target_day": date(2026, 9, 24),
        "timezone": "Asia/Shanghai",
        "window_start": datetime(2026, 9, 23, 16, tzinfo=timezone.utc),
        "window_end": datetime(2026, 9, 24, 16, tzinfo=timezone.utc),
        "config_snapshot_json": {"forum_ids": [5]},
        "report_revision": 2,
        "status": "partial",
        "coverage_status": "partial",
        "created_at": datetime(2026, 9, 25, tzinfo=timezone.utc),
        "report_json": {"facts": {}, "editorial": {}},
        "body_markdown": "日报正文",
        "stats_receipt_json": {
            "target_day": "2026-09-24",
            "counts": {"candidate_count_returned": 1},
            "coverage": {
                "coverage_status": "partial",
                "complete": False,
                "full_forum_coverage_proven": False,
                "gap_reasons": ["本地归档快照有限"],
            },
            "candidates": [{"tid": 42}],
        },
        "source_receipts_json": [{
            "receipt_id": "daily:2026-09-24:42:501:abc123",
            "tid": 42,
            "pid": 501,
            "published_at": "2026-09-24T10:00:00+00:00",
            "content_hash": "original-hash",
            "truncated": False,
        }],
        "gap_reasons_json": ["来源覆盖不完整"],
    }


@pytest.fixture
def fake_database(monkeypatch):
    conn = _Conn()
    revision = _revision()
    monkeypatch.setattr(queries, "connect", lambda *_a, **_kw: conn)
    monkeypatch.setattr(queries, "DailyBriefsRepository", lambda _conn: type(
        "Repo", (), {"get_report_revision": lambda _self, _id: revision}
    )())
    return conn, revision


def test_freeze_rejects_invalid_uuid_before_database_lookup(fake_database):
    with pytest.raises(queries.DailyBriefRevisionError) as error:
        queries.freeze_daily_report_revision(revision_id="daily-7", session_id="s")
    assert error.value.code == "CHAT_INVALID_REQUEST"
    assert error.value.status_code == 400


def test_freeze_hides_revision_owned_by_another_user(fake_database):
    conn, revision = fake_database
    revision["owner_id"] = "somebody-else"
    with pytest.raises(queries.DailyBriefRevisionError) as error:
        queries.freeze_daily_report_revision(revision_id=revision["revision_id"], session_id="s")
    assert error.value.code == "CHAT_DAILY_REPORT_UNAVAILABLE"
    assert conn.closed


def test_freeze_snapshots_report_scope_sources_and_gaps(fake_database):
    _conn, revision = fake_database
    snapshot = queries.freeze_daily_report_revision(
        revision_id=revision["revision_id"], session_id="s"
    )
    assert snapshot["revision_id"] == revision["revision_id"]
    assert snapshot["scope"]["forum_ids"] == [5]
    assert snapshot["scope"]["tids"] == [42]
    assert snapshot["scope"]["pids"] == [501]
    assert snapshot["sources"][0]["receipt_id"].startswith("daily:")
    assert snapshot["coverage"]["gap_reasons"] == ["来源覆盖不完整", "本地归档快照有限"]
    assert snapshot["body_markdown"] == "日报正文"


def test_read_marks_source_changed_without_rewriting_frozen_report(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(queries, "connect", lambda *_a, **_kw: conn)

    class Evidence:
        def __init__(self, _conn):
            pass

        def read_floor(self, _tid, _pid):
            return {"content": "current content"}

    monkeypatch.setattr(queries, "AssistantEvidenceRepository", Evidence)
    snapshot = {
        "revision_id": "c01b2a3e-0aef-4a68-b2a4-3e037b9b3f93",
        "issue_id": "issue-1", "report_revision": 2, "status": "partial",
        "created_at": "2026-09-25", "scope": {}, "body_markdown": "Frozen body",
        "body_truncated": False, "stats": {}, "coverage": {}, "source_count_total": 1,
        "sources": [{"receipt_id": "daily:...", "tid": 42, "pid": 501, "content_hash": "old-hash"}],
    }
    result = queries.read_daily_report_snapshot(snapshot=snapshot)
    assert result["body_markdown"] == "Frozen body"
    assert result["immutable"] is True
    assert result["sources"][0]["source_status"] == "source_changed"
    assert result["sources"][0]["url"].endswith("pid=501")
    assert conn.closed
