from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from yamibo_mcp.application.daily_brief_coverage import _thread_floor_evidence
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate


def test_thread_floor_evidence_groups_postgres_columns_and_checks_target_day(pg_engine):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    suffix = uuid.uuid4().hex[:8]
    forum_id = 700_000 + int(suffix, 16) % 100_000
    tid = 1_500_000_000 + int(suffix, 16) % 100_000_000
    pid = tid + 100_000
    setup = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        migrate(setup, schema="public")
    finally:
        setup.close()

    with pg_engine.begin() as raw:
        raw.execute(
            text("INSERT INTO forums(forum_id,name,content_kind,base_url,enabled) VALUES (:id,'coverage test','discussion','https://example.invalid',TRUE)"),
            {"id": forum_id},
        )
        raw.execute(
            text("""INSERT INTO threads(tid,page_type,raw_title,pub_time,forum_id,content_kind,
                     archive_status,capture_mode,local_reply_count,reply_count_mismatch_reason)
                   VALUES (:tid,'discussion','coverage test','2026-09-24 01:00:00+00',:forum_id,
                     'discussion','complete','text_only',0,NULL)"""),
            {"tid": tid, "forum_id": forum_id},
        )
        raw.execute(
            text("""INSERT INTO floors(pid,tid,floor_no,publisher,publisher_uid,content,pub_time)
                   VALUES (:pid,:tid,1,'tester','coverage-test','evidence','2026-09-24 02:00:00+00')"""),
            {"pid": pid, "tid": tid},
        )

    conn = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        evidence = _thread_floor_evidence(
            conn,
            tid,
            datetime(2026, 9, 24, tzinfo=timezone.utc),
            datetime(2026, 9, 25, tzinfo=timezone.utc),
        )
        assert evidence["eligible_discussion"] is True
        assert evidence["floor_count"] == 1
        assert evidence["target_day_floor_count"] == 1
        assert evidence["local_reply_count_consistent"] is True
    finally:
        conn.close()
        with pg_engine.begin() as raw:
            raw.execute(text("DELETE FROM floors WHERE tid=:tid"), {"tid": tid})
            raw.execute(text("DELETE FROM threads WHERE tid=:tid"), {"tid": tid})
            raw.execute(text("DELETE FROM forums WHERE forum_id=:forum_id"), {"forum_id": forum_id})
