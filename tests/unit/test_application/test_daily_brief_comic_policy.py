from datetime import datetime, timezone

import pytest

from yamibo_mcp.application.daily_brief_coverage import _thread_floor_evidence
from yamibo_mcp.application.daily_brief_service import _read_candidate_sources


@pytest.mark.parametrize("forum_id,kind,enabled,expected", [
    (30, "comic", True, True),
    (31, "comic", True, False),
    (30, "comic", False, False),
    (33, "discussion", True, True),
    (5, "discussion", True, True),
])
def test_daily_comic_policy_and_source_reader(db, forum_id, kind, enabled, expected):
    db.execute(
        "INSERT INTO forums(forum_id,name,content_kind,base_url,enabled) VALUES (?,?,?,?,?) ON CONFLICT(forum_id) DO UPDATE SET content_kind=excluded.content_kind,enabled=excluded.enabled",
        (forum_id, "test", kind, "https://example.invalid", enabled),
    )
    db.execute(
        """INSERT INTO threads(tid,page_type,raw_title,forum_id,content_kind,archive_status,
           capture_mode,local_reply_count) VALUES (100,'discussion','test',?,?,'complete','text_only',0)""",
        (forum_id, kind),
    )
    db.execute(
        """INSERT INTO floors(pid,tid,floor_no,content,pub_time)
           VALUES (101,100,1,'actual daily text','2026-09-24 02:00:00+00:00')"""
    )
    start = datetime(2026, 9, 24, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, tzinfo=timezone.utc)
    evidence = _thread_floor_evidence(db, 100, start, end)
    assert evidence["eligible_discussion"] is expected
    receipts = _read_candidate_sources(db, {
        "target_day": "2026-09-24",
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "candidates": [{"tid": 100, "source_receipts": [{"pid": 101}]}],
    })
    assert bool(receipts) is expected
