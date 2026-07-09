from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from yamibo_mcp.application.thread_resync_planner import plan_thread_resync_batch
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot


def _fake_settings(tmp_path: Path):
    settings = MagicMock()
    settings.db_path = str(tmp_path / "test.db")
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.novel_txt_export_dir = tmp_path / "novel_exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.novel_txt_export_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _make_snapshot(*, tid: int, floor_count: int = 3, pub_time: str = "2026-01-01") -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="[Test] Thread",
        display_title="Thread",
        group_name="Test",
        author_guess="Author",
        core_title_guess="Thread",
        normalized_core_title="thread",
        series_key="thread",
        title_aliases=[],
        chapter_name=None,
        chapter_index=None,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.9,
        needs_review=False,
        parser_version="title-v1",
    )
    floors = [
        FloorSnapshot(
            pid=1000 + i,
            tid=tid,
            floor_no=i,
            publisher="user",
            content=f"floor {i}",
            pub_time=pub_time,
            has_images=False,
        )
        for i in range(1, floor_count + 1)
    ]
    return ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/thread-{tid}-1-1.html",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="user",
        publisher_uid="1",
        pub_time=pub_time,
        permission=0,
        floors=floors,
        image_count=0,
    )


def test_backfill_scans_pages_and_orders_results(tmp_path, db):
    settings = _fake_settings(tmp_path)
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=101))
    repo.upsert_snapshot(_make_snapshot(tid=202))
    db.execute("UPDATE threads SET remote_reply_count = ?, remote_last_reply_at = ?, remote_last_replier = ?, remote_observed_at = ?, remote_observed_from = ? WHERE tid = ?", (5, "2026-01-02T00:00:00+00:00", "A", "2026-01-02T00:00:00+00:00", "u", 101))
    db.execute("UPDATE threads SET remote_reply_count = ?, remote_last_reply_at = ?, remote_last_replier = ?, remote_observed_at = ?, remote_observed_from = ? WHERE tid = ?", (1, "2026-01-01T00:00:00+00:00", "B", "2026-01-01T00:00:00+00:00", "u", 202))
    db.commit()

    with patch("yamibo_mcp.application.thread_resync_planner.load_settings", return_value=settings), patch("yamibo_mcp.application.thread_resync_planner.connect", return_value=db):
        result = plan_thread_resync_batch(tids=[202, 101], mode="backfill", max_detail_jobs=10)

    assert result.ok is True
    assert result.data["mode"] == "backfill"
    assert result.data["items"][0]["tid"] == 101
    assert result.data["items"][0]["decision"] == "needs_resync"
    assert result.data["items"][1]["decision"] == "unknown"


def test_daily_delta_marks_partial_as_maybe_changed(tmp_path, db):
    settings = _fake_settings(tmp_path)
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=303))
    db.execute("UPDATE threads SET archive_status = ?, remote_reply_count = ?, remote_last_reply_at = ? WHERE tid = ?", ("partial", 3, "2026-01-02T00:00:00+00:00", 303))
    db.commit()

    with patch("yamibo_mcp.application.thread_resync_planner.load_settings", return_value=settings), patch("yamibo_mcp.application.thread_resync_planner.connect", return_value=db):
        result = plan_thread_resync_batch(tids=[303], mode="daily_delta")

    item = result.data["items"][0]
    assert item["decision"] == "maybe_changed"
    assert "partial_archive" in item["reason_codes"]
    assert item["job_preview"]["create_sync_thread"] is False


def test_max_detail_jobs_truncates_stably(tmp_path, db):
    settings = _fake_settings(tmp_path)
    repo = ThreadsRepository(db)
    for tid in [1, 2, 3]:
        repo.upsert_snapshot(_make_snapshot(tid=tid))
    with patch("yamibo_mcp.application.thread_resync_planner.load_settings", return_value=settings), patch("yamibo_mcp.application.thread_resync_planner.connect", return_value=db):
        result = plan_thread_resync_batch(tids=[3, 2, 1], mode="daily_delta", max_detail_jobs=2)

    assert [item["tid"] for item in result.data["items"]] == [3, 2]


def test_dry_run_does_not_create_jobs(tmp_path, db):
    settings = _fake_settings(tmp_path)
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=404))

    with patch("yamibo_mcp.application.thread_resync_planner.load_settings", return_value=settings), patch("yamibo_mcp.application.thread_resync_planner.connect", return_value=db):
        result = plan_thread_resync_batch(tids=[404], mode="daily_delta", persist_observation=False)

    assert result.ok is True
    assert result.data["persist_observation"] is False


def test_remote_reply_count_lower_than_local_becomes_unknown(tmp_path, db):
    settings = _fake_settings(tmp_path)
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=405, floor_count=4))
    db.execute(
        "UPDATE threads SET remote_reply_count = ?, remote_last_reply_at = ?, remote_observed_at = ?, remote_observed_from = ? WHERE tid = ?",
        (1, "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00", "https://bbs.yamibo.com/forum.php?mod=forumdisplay&fid=30&page=1", 405),
    )
    db.commit()

    with patch("yamibo_mcp.application.thread_resync_planner.load_settings", return_value=settings), patch("yamibo_mcp.application.thread_resync_planner.connect", return_value=db):
        result = plan_thread_resync_batch(tids=[405], mode="daily_delta", include_unknown=False)

    item = result.data["items"][0]
    assert item["decision"] == "unknown"
    assert item["reason_codes"] == ["reply_count_mismatch"]
    assert item["local_state"]["local_reply_count"] == 3
    assert item["remote_observation"]["remote_reply_count"] == 1
    assert item["job_preview"]["create_sync_thread"] is False


def test_pages_collect_tids_and_report_scan_metadata(tmp_path, db):
    settings = _fake_settings(tmp_path)
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(tid=501))
    fake_page = {
        "forum_url": "https://bbs.yamibo.com/forum-5-1.html",
        "count": 1,
        "source": "forum_page",
        "items": [{"tid": 501}],
    }
    with patch("yamibo_mcp.application.thread_resync_planner.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.thread_resync_planner.connect", return_value=db), \
         patch("yamibo_mcp.application.thread_resync_planner.browse_forum_page", return_value=fake_page) as mock_browse:
        result = plan_thread_resync_batch(forum_id=5, pages=[1], mode="daily_delta")

    assert mock_browse.call_args.kwargs["forum_id"] == 5
    assert result.data["coverage"]["scanned_pages"] == [1]
    assert result.data["coverage"]["scanned_tids"] == [501]
    assert result.data["items"][0]["tid"] == 501
