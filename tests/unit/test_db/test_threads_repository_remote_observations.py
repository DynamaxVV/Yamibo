from __future__ import annotations

from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot


def _make_snapshot(tid: int) -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="[A组] 测试漫画 第1话",
        display_title="测试漫画 第1话",
        group_name="A组",
        author_guess="作者A",
        core_title_guess="测试漫画",
        normalized_core_title="测试漫画",
        series_key="测试漫画",
        title_aliases=[],
        chapter_name="第1话",
        chapter_index=1.0,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.9,
        needs_review=False,
        parser_version="title-v1",
    )
    floor = FloorSnapshot(
        pid=tid * 10 + 1,
        tid=tid,
        floor_no=1,
        publisher="user1",
        content="测试内容",
        pub_time="2025-01-01T00:00:00",
        has_images=False,
        image_urls=[],
    )
    return ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/thread-{tid}-1-1.html",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="user1",
        publisher_uid="12345",
        pub_time="2025-01-01T00:00:00",
        permission=0,
        floors=[floor],
        image_count=0,
    )


def test_update_remote_observation_snapshot_persists_latest_tail(db):
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(3501))

    repo.update_remote_observation_snapshot(
        tid=3501,
        forum_id=55,
        source_kind="forum_list",
        observation_type="latest_tail",
        remote_last_reply_at_raw="2026-07-05 12:34",
        remote_last_reply_at="2026-07-05T12:34:00+00:00",
        remote_last_replier="最后回复者",
        remote_reply_count=12,
        observed_at="2026-07-05T12:35:00+00:00",
        observed_from="https://bbs.yamibo.com/forum.php?mod=forumdisplay&fid=55&page=1",
    )

    row = repo.get_thread(3501)
    assert row["remote_last_reply_at_raw"] == "2026-07-05 12:34"
    assert row["remote_last_reply_at"] == "2026-07-05T12:34:00+00:00"
    assert row["remote_last_replier"] == "最后回复者"
    assert row["remote_reply_count"] == 12
    assert row["remote_observed_at"] == "2026-07-05T12:35:00+00:00"
    assert row["remote_observed_from"].startswith("https://bbs.yamibo.com/")


def test_update_remote_observation_snapshot_ignores_non_latest_tail(db):
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(3502))
    before = repo.get_thread(3502)

    repo.update_remote_observation_snapshot(
        tid=3502,
        forum_id=55,
        source_kind="search",
        observation_type="discovery",
        remote_last_reply_at_raw="2026-07-05 09:00",
        remote_last_reply_at="2026-07-05T09:00:00+00:00",
        remote_last_replier="不应写入",
        remote_reply_count=99,
        observed_at="2026-07-05T09:01:00+00:00",
        observed_from="https://bbs.yamibo.com/search.php?mod=forum",
    )

    row = repo.get_thread(3502)
    for field in (
        "remote_last_reply_at_raw",
        "remote_last_reply_at",
        "remote_last_replier",
        "remote_reply_count",
        "remote_observed_at",
        "remote_observed_from",
    ):
        assert row[field] == before[field]


def test_probe_archive_states_returns_remote_observation_fields(db):
    repo = ThreadsRepository(db)
    repo.upsert_snapshot(_make_snapshot(3503))
    db.execute(
        """
        UPDATE threads
        SET local_reply_count = ?, reply_count_checked_at = ?, reply_count_mismatch_reason = ?,
            remote_last_reply_at_raw = ?, remote_last_reply_at = ?, remote_last_replier = ?,
            remote_reply_count = ?, remote_observed_at = ?, remote_observed_from = ?
        WHERE tid = ?
        """,
        (
            8,
            "2026-07-05T13:00:00+00:00",
            "remote_gt_local",
            "2026-07-05 12:34",
            "2026-07-05T12:34:00+00:00",
            "最后回复者",
            9,
            "2026-07-05T12:35:00+00:00",
            "https://bbs.yamibo.com/forum.php?mod=forumdisplay&fid=55&page=1",
            3503,
        ),
    )
    db.commit()

    item = repo.probe_archive_states([3503])[0]

    assert item["local_reply_count"] == 8
    assert item["reply_count_checked_at"] == "2026-07-05T13:00:00+00:00"
    assert item["reply_count_mismatch_reason"] == "remote_gt_local"
    assert item["remote_last_reply_at_raw"] == "2026-07-05 12:34"
    assert item["remote_last_reply_at"] == "2026-07-05T12:34:00+00:00"
    assert item["remote_last_replier"] == "最后回复者"
    assert item["remote_reply_count"] == 9
    assert item["remote_observed_at"] == "2026-07-05T12:35:00+00:00"
    assert item["remote_observed_from"].startswith("https://bbs.yamibo.com/")
