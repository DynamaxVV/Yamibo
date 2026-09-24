from __future__ import annotations

import threading
import time
import json
from types import SimpleNamespace
from contextlib import contextmanager
from dataclasses import replace

import pytest

from yamibo_mcp.daemon.handlers.image_backfill import (
    _diff_snapshot_images,
    _local_assets_by_url,
    _image_slot_overrides_from_assets,
    _merge_assets,
    _merge_page_snapshots,
    _merge_remote_into_local,
    _missing_urls_from_assets,
    _refresh_metadata_from_asset_rows,
    _reconcile_missing_targets,
    _foreground_work_available,
    _expected_target_paths,
    _metadata_missing_targets,
    _metadata_target_positions,
    _resolve_selected_remote_urls,
    _selected_download_retries,
    _image_download_failure_code,
    _image_retry_delay_seconds,
    _needs_attachment_url_refresh,
    _should_retry_image_download,
    _snapshot_for_missing_images,
    _stable_attachment_id,
    _successful_selected_url_aliases,
    _url_was_successfully_downloaded,
    handle_image_backfill,
)
from yamibo_mcp.daemon.image_backfill_scheduler import maybe_enqueue_image_backfill_dry_run
from yamibo_mcp.daemon import image_backfill_scheduler as scheduler
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.system_state import SystemStateRepository
from yamibo_mcp.errors import RemoteFetchError, ThreadPermissionRequiredError
from yamibo_mcp.storage.images import ImageDownloadResult, download_images_to_staging
from yamibo_mcp.domain.models import AssetSnapshot, FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.yamibo.urls import is_yamibo_site_image_url


def _snapshot(tid: int, image_urls: list[str]) -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="title",
        display_title="title",
        group_name=None,
        author_guess=None,
        core_title_guess="title",
        normalized_core_title="title",
        series_key="title",
        title_aliases=[],
        chapter_name=None,
        chapter_index=None,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=1.0,
        needs_review=False,
    )
    return ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}",
        page_type="thread",
        raw_title="title",
        display_title="title",
        title=title,
        publisher="a",
        publisher_uid=None,
        pub_time=None,
        permission=0,
        floors=[
            FloorSnapshot(
                pid=tid * 10 + 1,
                tid=tid,
                floor_no=1,
                publisher="a",
                content="first",
                pub_time=None,
                has_images=False,
            ),
            FloorSnapshot(
                pid=tid * 10 + 2,
                tid=tid,
                floor_no=2,
                publisher="b",
                content="reply",
                pub_time=None,
                has_images=True,
                image_urls=image_urls,
            ),
        ],
        image_count=len(image_urls),
    )


def test_merge_page_snapshots_reindexes_page_local_floor_numbers():
    base = _snapshot(127, [])
    page_one = replace(
        base,
        floors=[
            replace(base.floors[0], pid=1271, floor_no=1),
            replace(base.floors[1], pid=1272, floor_no=2),
        ],
    )
    page_two = replace(
        base,
        floors=[
            replace(base.floors[0], pid=1273, floor_no=1),
            replace(base.floors[1], pid=1274, floor_no=2),
        ],
    )

    merged = _merge_page_snapshots([page_one, page_two])

    assert [floor.pid for floor in merged.floors] == [1271, 1272, 1273, 1274]
    assert [floor.floor_no for floor in merged.floors] == [1, 2, 3, 4]


def test_merge_remote_into_local_preserves_existing_floor_numbers_and_appends_new_pids():
    local = _snapshot(128, [])
    remote = replace(
        local,
        floors=[
            replace(
                local.floors[1],
                floor_no=1,
                image_urls=["https://example.invalid/refreshed.jpg"],
            ),
            replace(local.floors[0], pid=1283, floor_no=2, content="new reply"),
        ],
    )

    merged = _merge_remote_into_local(local, remote)

    assert [(floor.pid, floor.floor_no) for floor in merged.floors] == [
        (1281, 1),
        (1282, 2),
        (1283, 3),
    ]
    assert merged.floors[1].image_urls == ["https://example.invalid/refreshed.jpg"]


def test_image_backfill_diff_skips_existing_static_shared_asset(tmp_path):
    paths = StoragePaths(tmp_path)
    static_url = "https://bbs.yamibo.com/static/image/smiley/gexing/008.gif"
    paths.shared_asset_path(static_url).parent.mkdir(parents=True, exist_ok=True)
    paths.shared_asset_path(static_url).write_bytes(b"gif")

    result = _diff_snapshot_images(_snapshot(123, [static_url]), local_assets={}, paths=paths)

    assert result["existing_non_first_images_ok"] == 1
    assert result["need_fetch_count"] == 0
    assert result["shared_static_missing_count"] == 0


def test_image_backfill_diff_counts_content_missing_separately_from_static(tmp_path):
    paths = StoragePaths(tmp_path)
    static_url = "https://bbs.yamibo.com/static/image/common/back.gif"
    content_url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/image.jpg"

    result = _diff_snapshot_images(_snapshot(124, [static_url, content_url]), local_assets={}, paths=paths)

    assert result["need_fetch_count"] == 1
    assert result["content_need_fetch_count"] == 1
    assert result["shared_static_missing_count"] == 1
    assert result["need_fetch_by_class"]["content"] == 1
    assert result["need_fetch_by_class"]["decorative"] == 1


def test_yamibo_site_image_url_requires_a_first_party_image_endpoint():
    assert is_yamibo_site_image_url("https://bbs.yamibo.com/static/image/smiley/gexing/008.gif")
    assert is_yamibo_site_image_url("https://bbs.yamibo.com/data/attachment/forum/202501/01/image.jpg")
    assert is_yamibo_site_image_url("https://bbs.yamibo.com/forum.php?mod=attachment&aid=MQ%3D%3D")
    assert not is_yamibo_site_image_url("http://mis.im.tku.edu.tw/~fireflyyen19a/new/src/image.jpg")
    assert not is_yamibo_site_image_url("https://example.org/data/attachment/forum/image.jpg")
    assert not is_yamibo_site_image_url("https://bbs.yamibo.com/forum.php?mod=viewthread&tid=123")


def test_image_backfill_diff_site_only_skips_external_image_urls(tmp_path):
    paths = StoragePaths(tmp_path)
    external_url = "http://mis.im.tku.edu.tw/~fireflyyen19a/new/src/image.jpg"
    site_url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/image.jpg"

    result = _diff_snapshot_images(
        _snapshot(125, [external_url, site_url]),
        local_assets={},
        paths=paths,
        include_first_floor=True,
        site_only=True,
    )

    assert result["remote_image_count"] == 2
    assert result["need_fetch_count"] == 1
    assert result["missing_items_for_apply"] == [{
        "pid": 1252,
        "floor_no": 2,
        "url": site_url,
        "class": "content",
        "reason": "not_in_assets",
    }]


def test_metadata_missing_targets_site_only_excludes_external_urls(tmp_path):
    tid = 126
    paths = StoragePaths(tmp_path)
    external_url = "http://mis.im.tku.edu.tw/~fireflyyen19a/new/src/image.jpg"
    site_url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MQ%3D%3D"
    static_url = "https://bbs.yamibo.com/static/image/smiley/gexing/008.gif"
    metadata_path = paths.thread_metadata(tid)
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(json.dumps({
        "floors": [{
            "remote_image_urls": [external_url, site_url, static_url],
            "image_slots": [
                {"remote_url": external_url, "local_path": None, "status": "missing"},
                {"remote_url": site_url, "local_path": None, "status": "missing"},
                {"remote_url": static_url, "local_path": None, "status": "missing"},
            ],
        }],
    }), encoding="utf-8")

    assert _metadata_missing_targets(paths, tid) == [external_url, site_url, static_url]
    assert _metadata_missing_targets(paths, tid, site_only=True) == [site_url]


def test_selected_attachment_signature_rotation_matches_stable_aid():
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHw1MWU2OTRkM3wxNzg3MjIyNzU3fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHwzNTJjOTA2M3wxNzg3MzI1MzE5fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    assert _stable_attachment_id(old) == "1638434"
    assert _stable_attachment_id(current) == "1638434"
    snapshot = _snapshot(575090, [current])
    target = {old: {"pid": 575090 * 10 + 2, "floor_no": 2, "image_index": 1}}
    assert _resolve_selected_remote_urls(snapshot, [old], target) == {old: current}


def test_attachment_url_refresh_only_for_stale_link_evidence():
    attachment = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MQ%3D%3D"
    direct_attachment = "https://bbs.yamibo.com/data/attachment/forum/image.png"
    static_image = "https://bbs.yamibo.com/static/image/smiley/default/1.gif"
    assert _needs_attachment_url_refresh({attachment}, [{"url": attachment, "http_status": 404}])
    assert not _needs_attachment_url_refresh({attachment}, [{"url": attachment, "error_type": "network_error"}])
    assert not _needs_attachment_url_refresh({attachment}, [{"url": attachment, "http_status": 403, "error_type": "waf_response"}])
    assert _needs_attachment_url_refresh({direct_attachment}, [{"url": direct_attachment, "http_status": 404}])
    assert not _needs_attachment_url_refresh({static_image}, [{"url": static_image, "http_status": 404}])


def test_selected_attachment_identity_mismatch_is_not_silently_retargeted():
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MQ%3D%3D"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=Mg%3D%3D"
    snapshot = _snapshot(575091, [current])
    target = {old: {"pid": 575091 * 10 + 2, "floor_no": 2, "image_index": 1}}
    assert _resolve_selected_remote_urls(snapshot, [old], target) == {}


def test_selected_external_url_does_not_follow_a_changed_position():
    old = "https://external.invalid/old.jpg"
    current = "https://external.invalid/replacement.jpg"
    snapshot = _snapshot(575092, [current])
    target = {old: {"pid": 575092 * 10 + 2, "floor_no": 2, "image_index": 1}}
    assert _resolve_selected_remote_urls(snapshot, [old], target) == {}


def test_rotated_selected_success_clears_the_submitted_missing_url():
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MQ%3D%3D"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MXxuZXc%3D"
    assert _successful_selected_url_aliases({current}, {old: current}) == {old, current}


def test_successful_download_matches_rotated_missing_attachment_signature():
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHw1MWU2OTRkM3wxNzg3MjIyNzU3fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHwzNTJjOTA2M3wxNzg3MzI1MzE5fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    assert _url_was_successfully_downloaded(old, {current}) is True
    assert _url_was_successfully_downloaded("https://external.invalid/old.jpg", {"https://external.invalid/new.jpg"}) is False


def test_selected_asset_state_keeps_only_the_downloaded_slot_available():
    urls = [f"https://bbs.yamibo.com/data/attachment/forum/{index}.jpg" for index in range(1, 66)]
    assets = [
        AssetSnapshot(
            f"asset-{index}",
            1,
            2,
            "attachment",
            url,
            "images/floor_001_13.jpg" if index == 13 else None,
            False,
            False,
            "downloaded" if index == 13 else "pending",
        )
        for index, url in enumerate(urls, start=1)
    ]
    overrides = _image_slot_overrides_from_assets(assets)
    missing, missing_shared = _missing_urls_from_assets(assets)
    assert overrides[urls[0]] == {"local_path": None, "status": "missing"}
    assert overrides[urls[12]] == {"local_path": "images/floor_001_13.jpg", "status": "non_export"}
    assert len(missing) == 64
    assert urls[12] not in missing
    assert missing_shared == []


def test_reconcile_refreshes_rotated_asset_into_its_metadata_slot(tmp_path):
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHw1MWU2OTRkM3wxNzg3MjIyNzU3fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHxmYjQzZDc3ZnwxNzg3MzI5ODk5fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    missing_url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNXxiMTc0OGE1ZXwxNzg3MzI5ODk5fDczNzQ5M3w1NzUwOTA%3D&nothumb=yes"
    paths = StoragePaths(tmp_path)
    image_path = paths.thread_images_dir(575090) / "floor_001_13.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    image_path.write_bytes(png)
    metadata = {
        "floors": [{
            "pid": 41605934,
            "remote_image_urls": [missing_url, current],
            "image_slots": [
                {"remote_url": missing_url, "local_path": "images/floor_001_13.png", "status": "non_export"},
                {"remote_url": current, "local_path": None, "status": "missing"},
            ],
        }],
        "archive_status": "complete",
        "missing_image_urls": [],
    }
    rows = [
        {"remote_url": missing_url, "local_path": None, "exportable": False},
        {"remote_url": old, "local_path": "images/floor_001_13.png", "exportable": False},
    ]

    missing, missing_shared = _refresh_metadata_from_asset_rows(
        paths=paths,
        tid=575090,
        metadata=metadata,
        asset_rows=rows,
    )

    slots = metadata["floors"][0]["image_slots"]
    assert slots[0] == {"remote_url": missing_url, "local_path": None, "status": "missing"}
    assert slots[1] == {"remote_url": current, "local_path": "images/floor_001_13.png", "status": "non_export"}
    assert missing == [missing_url]
    assert missing_shared == []
    assert metadata["archive_status"] == "partial"


def test_rotated_selected_asset_keeps_existing_local_path_for_other_assets(tmp_path):
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MQ%3D%3D"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MXwz%3D"
    untouched = "https://bbs.yamibo.com/data/attachment/forum/other.jpg"
    assets = [
        AssetSnapshot("asset-old", 1, 2, "attachment", current, None, True, True, "pending"),
        AssetSnapshot("asset-other", 1, 2, "image", untouched, "images/floor_002_02.jpg", True, True, "downloaded"),
    ]
    local = {
        old: SimpleNamespace(remote_url=old, asset_type="attachment", local_path=None, status="missing"),
        untouched: SimpleNamespace(remote_url=untouched, asset_type="image", local_path="images/floor_002_02.jpg", status="downloaded"),
    }
    result = SimpleNamespace(missing_urls=[], missing_shared_urls=[])
    merged = _merge_assets(
        assets,
        local_assets=local,
        local_path_by_url={current: "images/floor_002_01.jpg"},
        image_result=result,
    )
    assert merged[0].local_path == "images/floor_002_01.jpg"
    assert merged[1].local_path == "images/floor_002_02.jpg"


def test_selected_first_floor_download_keeps_original_image_index(tmp_path):
    from dataclasses import replace

    source = tmp_path / "source.png"
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    source.write_bytes(png)
    target_url = source.as_uri()
    urls = [f"https://example.invalid/{index}.jpg" for index in range(1, 23)] + [target_url]
    base = _snapshot(125, [])
    first = replace(base.floors[0], publisher="a", has_images=True, image_urls=urls)
    snapshot = replace(base, floors=[first], image_count=len(urls))
    missing = _snapshot_for_missing_images(snapshot, [{"pid": first.pid, "floor_no": 1, "url": target_url}])

    result = download_images_to_staging(
        StoragePaths(tmp_path),
        "image_backfill_selected",
        missing,
        target_urls={target_url},
        timeout=1,
    )

    assert result.missing_urls == []
    assert result.relative_path_by_url[target_url] == "images/floor_001_23.png"
    assert (tmp_path / "staging/jobs/image_backfill_selected/images/floor_001_23.png").exists()


def test_selected_download_attempts_non_publisher_image_and_external_is_one_shot(tmp_path):
    from dataclasses import replace

    source = tmp_path / "reply.png"
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    source.write_bytes(png)
    target_url = source.as_uri()
    base = _snapshot(126, [])
    reply = replace(
        base.floors[0],
        pid=1262,
        floor_no=2,
        publisher="reply-author",
        has_images=True,
        image_urls=[target_url],
    )
    snapshot = replace(base, floors=[reply], image_count=1)

    result = download_images_to_staging(
        StoragePaths(tmp_path),
        "image_backfill_selected_reply",
        snapshot,
        target_urls={target_url},
        timeout=1,
    )

    assert result.relative_path_by_url[target_url] == "images/floor_002_01.png"
    assert _selected_download_retries(
        target_urls={"https://third-party.invalid/dead.jpg"},
        configured_retries=3,
    ) == 0
    assert _selected_download_retries(
        target_urls={"https://bbs.yamibo.com/forum.php?mod=attachment&aid=1"},
        configured_retries=3,
    ) == 3


def test_selected_image_download_retries_only_transient_failures():
    assert _should_retry_image_download(
        ImageDownloadResult(stopped_reason="stage_timeout"),
        [],
    ) is True
    assert _should_retry_image_download(
        ImageDownloadResult(),
        [{"status": "error", "error_type": "truncated_image", "retryable": True}],
    ) is True
    assert _should_retry_image_download(
        ImageDownloadResult(),
        [{"status": "error", "error_type": "http_error", "retryable": False}],
    ) is False
    assert _should_retry_image_download(
        ImageDownloadResult(),
        [{"status": "error", "error_type": "waf_response", "retryable": False}],
    ) is True
    assert _image_download_failure_code(
        [{"status": "error", "error_type": "waf_response", "retryable": False}],
    ) == "REMOTE_SOFT_BLOCK"
    assert _image_download_failure_code(
        [{"status": "error", "error_type": "truncated_image", "retryable": True}],
    ) == "IMAGE_TARGET_NOT_DOWNLOADED"


def test_image_retry_delay_uses_retry_after_and_bounded_backoff():
    diagnostics = [{"http_status": 429, "response_headers": {"Retry-After": "180"}}]
    assert _image_retry_delay_seconds(diagnostics, retry_count=0) == 180
    assert _image_retry_delay_seconds(diagnostics, retry_count=3) == 480
    assert _image_retry_delay_seconds([{"http_status": 503}], retry_count=0) == 60
    assert _image_retry_delay_seconds(
        [{"http_status": 429, "response_headers": {"Retry-After": "99999"}}], retry_count=0,
    ) == 3600


def test_auto_scheduler_creates_internal_image_backfill_dry_run_job(db, tmp_path):
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (1001, 'raw', 'display', '2026-07-01T00:00:00+00:00', 2, 'complete', 5)
        """
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (2001, 1001, 2, 'reply', 1)
        """
    )
    db.commit()

    settings = SimpleNamespace(
        image_backfill_enabled=True,
        image_backfill_dry_run=True,
        image_backfill_forum_id=5,
        image_backfill_auto_interval_seconds=0.0,
        image_backfill_daily_limit=10,
        image_backfill_max_pages=1,
        image_backfill_fixed_after="2026-07-02T00:00:00+00:00",
    )

    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), settings) is True

    job = db.execute("SELECT * FROM jobs WHERE job_type = 'image_backfill'").fetchone()
    assert job is not None
    assert job["tid"] == 1001
    assert '"dry_run": true' in job["payload_json"]
    assert '"internal_auto": true' in job["payload_json"]


def test_auto_scheduler_apply_can_follow_prior_dry_run(db):
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (1002, 'raw', 'display', '2026-07-01T00:00:00+00:00', 2, 'complete', 5)
        """
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (2002, 1002, 2, 'reply', 1)
        """
    )
    db.commit()
    repo = JobsRepository(db)
    dry_job = repo.create("image_backfill", tid=1002, payload={"tid": 1002, "dry_run": True})
    repo.succeed(dry_job.job_id, {"dry_run": True})
    settings = SimpleNamespace(
        image_backfill_enabled=True,
        image_backfill_dry_run=False,
        image_backfill_forum_id=5,
        image_backfill_auto_interval_seconds=0.0,
        image_backfill_daily_limit=10,
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
    )

    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True

    jobs = db.execute("SELECT payload_json FROM jobs WHERE job_type = 'image_backfill' AND tid = 1002 ORDER BY created_at").fetchall()
    assert len(jobs) == 2
    assert '"dry_run": false' in jobs[-1]["payload_json"]


def _scheduler_settings(*, dry_run: bool = True, interval: float = 0.0, daily_limit: int = 10):
    return SimpleNamespace(
        image_backfill_enabled=True,
        image_backfill_dry_run=dry_run,
        image_backfill_forum_id=5,
        image_backfill_auto_interval_seconds=interval,
        image_backfill_daily_limit=daily_limit,
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
    )


def _insert_gap_thread(db, tid: int) -> None:
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 1, 'complete', 5)
        """,
        (tid,),
    )
    db.commit()


def test_auto_scheduler_skips_external_only_and_accepts_mixed_site_candidate(db, tmp_path):
    external_only_tid = 3051
    mixed_tid = 3052
    external_url = "http://mis.im.tku.edu.tw/~fireflyyen19a/new/src/image.jpg"
    site_url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/image.jpg"
    for tid, image_count in ((external_only_tid, 1), (mixed_tid, 2)):
        db.execute(
            """
            INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
            VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', ?, 'complete', 5)
            """,
            (tid, image_count),
        )
    db.commit()

    paths = StoragePaths(tmp_path)
    for tid, urls in (
        (external_only_tid, [external_url]),
        (mixed_tid, [external_url, site_url]),
    ):
        metadata_path = paths.thread_metadata(tid)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps({
            "floors": [{
                "remote_image_urls": urls,
                "image_slots": [
                    {"remote_url": url, "local_path": None, "status": "missing"}
                    for url in urls
                ],
            }],
            "missing_image_urls": urls,
        }), encoding="utf-8")

    settings = _scheduler_settings()
    settings.data_dir = tmp_path
    scan = scheduler._select_candidate_batch(
        JobsRepository(db), settings, dry_run=True, cursor_tid=0, campaign="metadata_reconcile_v1"
    )

    assert scan["candidate"]["tid"] == mixed_tid
    assert scan["candidate_count"] == 1
    assert scan["non_site_candidate_count"] == 1


def test_auto_scheduler_skips_static_only_candidate(db, tmp_path):
    tid = 3053
    static_url = "https://bbs.yamibo.com/static/image/smiley/gexing/008.gif"
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 1, 'complete', 5)
        """,
        (tid,),
    )
    db.execute(
        "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, 2, 'reply', 1)",
        (tid * 10 + 2, tid),
    )
    db.commit()

    paths = StoragePaths(tmp_path)
    metadata_path = paths.thread_metadata(tid)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps({
        "floors": [{
            "remote_image_urls": [static_url],
            "image_slots": [{"remote_url": static_url, "local_path": None, "status": "missing"}],
        }],
        "missing_shared_image_urls": [static_url],
    }), encoding="utf-8")

    settings = _scheduler_settings()
    settings.data_dir = tmp_path
    scan = scheduler._select_candidate_batch(
        JobsRepository(db), settings, dry_run=False, cursor_tid=0, campaign="metadata_reconcile_v1"
    )

    assert scan["candidate"] is None
    assert scan["candidate_count"] == 0
    assert scan["non_site_candidate_count"] == 1


def test_auto_scheduler_persists_cursor_and_wraps_without_second_scan(db):
    _insert_gap_thread(db, 3001)
    repo = JobsRepository(db)
    settings = _scheduler_settings()

    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state is not None
    assert state["scan_cursor_tid"] == 3001
    assert state["last_checked_at"]

    # An active automatic job suppresses further candidate scans. Once it is
    # complete, the next bounded cycle reaches the end and only wraps.
    active_job = db.execute(
        "SELECT job_id FROM jobs WHERE job_type = 'image_backfill' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    repo.succeed(active_job["job_id"])
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["scan_cursor_tid"] == 0
    assert state["scan_wrap_count"] == 1


def test_auto_scheduler_batch_is_bounded_and_cursor_uses_batch_end(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    for tid in (3101, 3102, 3103):
        _insert_gap_thread(db, tid)
    monkeypatch.setattr(scheduler, "_SCAN_BATCH_SIZE", 2)

    repo = JobsRepository(db)
    assert maybe_enqueue_image_backfill_dry_run(repo, _scheduler_settings()) is True
    job = db.execute(
        "SELECT tid FROM jobs WHERE job_type = 'image_backfill' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert job["tid"] == 3101
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["scan_cursor_tid"] == 3102


def test_auto_scheduler_consumes_persisted_candidates_without_rescanning(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    for tid in (3151, 3152, 3153):
        _insert_gap_thread(db, tid)
    monkeypatch.setattr(scheduler, "_SCAN_BATCH_SIZE", 3)
    scan_calls = []
    original_scan = scheduler._select_candidate_batch

    def count_scans(*args, **kwargs):
        scan_calls.append(True)
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(scheduler, "_select_candidate_batch", count_scans)
    repo = JobsRepository(db)
    settings = _scheduler_settings()

    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True
    first_job = db.execute(
        "SELECT job_id, tid FROM jobs WHERE job_type = 'image_backfill' ORDER BY created_at LIMIT 1"
    ).fetchone()
    assert first_job["tid"] == 3151
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert [candidate["tid"] for candidate in state["pending_candidates"]] == [3152, 3153]
    repo.succeed(first_job["job_id"])

    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True
    jobs = db.execute(
        "SELECT tid FROM jobs WHERE job_type = 'image_backfill' ORDER BY tid"
    ).fetchall()
    assert [job["tid"] for job in jobs] == [3151, 3152]
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert [candidate["tid"] for candidate in state["pending_candidates"]] == [3153]
    assert len(scan_calls) == 1


def test_auto_scheduler_does_not_scan_while_automatic_backfill_is_active(db, monkeypatch):
    repo = JobsRepository(db)
    repo.create("image_backfill", tid=3160, payload={"internal_auto": True})
    monkeypatch.setattr(
        "yamibo_mcp.daemon.image_backfill_scheduler._select_candidate_batch",
        lambda *args, **kwargs: pytest.fail("active backfill must prevent a candidate rescan"),
    )

    assert maybe_enqueue_image_backfill_dry_run(repo, _scheduler_settings()) is False
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["last_reason"] == "auto_backfill_active"
    assert state["pending_candidates"] == []


def test_auto_scheduler_discards_batch_when_scan_settings_change(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    db.execute(
        """
        INSERT INTO system_state (key, value_json, updated_at)
        VALUES (?, ?, ?)
        """,
        (
            "image_backfill_auto_scheduler",
            json.dumps({
                "scan_cursor_tid": 5000,
                "scan_wrap_count": 2,
                "scan_forum_id": 1,
                "pending_forum_id": 1,
                "pending_dry_run": True,
                "pending_candidates": [{"tid": 5001, "reason": "old_batch"}],
            }),
            "2026-09-23T00:00:00+00:00",
        ),
    )
    db.commit()
    settings = _scheduler_settings()
    settings.image_backfill_forum_id = 2
    scanned_cursors = []

    def no_candidates(*args, **kwargs):
        scanned_cursors.append(kwargs["cursor_tid"])
        return {
            "candidate": None,
            "candidates": [],
            "cursor_after": 0,
            "wrap_count": 0,
            "batch_size": 0,
            "candidate_count": 0,
            "non_site_candidate_count": 0,
            "blocking_count": 0,
            "elapsed_ms": 0.0,
        }

    monkeypatch.setattr(scheduler, "_select_candidate_batch", no_candidates)
    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), settings) is False

    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert scanned_cursors == [0]
    assert state["pending_candidates"] == []
    assert state["scan_forum_id"] == 2


def test_auto_scheduler_interval_throttles_no_candidate_scan(db, monkeypatch):
    _insert_gap_thread(db, 3201)
    repo = JobsRepository(db)
    settings = _scheduler_settings(interval=60.0)
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is True

    calls = []
    monkeypatch.setattr(
        "yamibo_mcp.daemon.image_backfill_scheduler._select_candidate_batch",
        lambda *args, **kwargs: calls.append(True),
    )
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    assert calls == []


def test_auto_scheduler_releases_single_flight_lock_after_failure(db, monkeypatch):
    repo = JobsRepository(db)
    settings = _scheduler_settings()
    calls = []

    def fail_once(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("candidate query failed")

    monkeypatch.setattr(
        "yamibo_mcp.daemon.image_backfill_scheduler._select_candidate_batch",
        fail_once,
    )
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    assert maybe_enqueue_image_backfill_dry_run(repo, settings) is False
    assert len(calls) == 2


def test_auto_scheduler_local_single_flight_is_nonblocking(db):
    settings = _scheduler_settings()
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    assert scheduler._SCHEDULER_LOCK.acquire(blocking=False)
    try:
        assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), settings) is False
    finally:
        scheduler._SCHEDULER_LOCK.release()


def test_auto_scheduler_disabled_short_circuits_before_query_and_state_write(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    state_repo = SystemStateRepository(db)
    state_repo.set_json("image_backfill_auto_scheduler", {"sentinel": "unchanged"})
    monkeypatch.setattr(
        scheduler,
        "_select_candidate_batch",
        lambda *args, **kwargs: pytest.fail("disabled scheduler must not scan candidates"),
    )
    settings = _scheduler_settings()
    settings.image_backfill_enabled = False

    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), settings) is False
    assert state_repo.get_json("image_backfill_auto_scheduler") == {"sentinel": "unchanged"}
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_auto_scheduler_no_candidate_advances_last_checked_and_cursor(db):
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (3400, 'raw', 'display', '2026-07-01T00:00:00+00:00', 0, 'complete', 5)
        """
    )
    db.commit()
    before = time.time()
    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), _scheduler_settings()) is False
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["scan_cursor_tid"] == 3400
    assert state["last_checked_at"] >= before
    assert state["last_reason"] == "scanned_no_candidate"
    assert db.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_auto_scheduler_preserves_all_candidate_reason_semantics(db, monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    # Keep each call to one TID so the returned candidate reason maps exactly
    # to the cursor position under test.
    monkeypatch.setattr(scheduler, "_SCAN_BATCH_SIZE", 1)

    def insert_thread(conn, tid: int, image_count: int) -> None:
        conn.execute(
            """
            INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
            VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', ?, 'complete', 5)
            """,
            (tid, image_count),
        )

    # Non-first-floor image without an asset.
    insert_thread(db, 3410, 0)
    db.execute("INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (34101, 3410, 2, 'reply', 1)")
    # Thread image count is greater than the number of image assets.
    insert_thread(db, 3420, 1)
    # Asset count matches image_count, but the existing image is pending.
    insert_thread(db, 3430, 1)
    db.execute(
        """
        INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, status)
        VALUES ('asset-3430', 3430, 34301, 'image', 'https://example.invalid/3430.jpg', NULL, 'pending')
        """
    )
    db.commit()

    repo = JobsRepository(db)
    reasons = []
    cursor = 0
    for expected_tid, expected_reason in (
        (3410, "non_first_floor_has_images_without_asset"),
        (3420, "image_count_asset_gap"),
        (3430, "missing_or_pending_asset"),
    ):
        scan = scheduler._select_candidate_batch(repo, _scheduler_settings(), dry_run=True, cursor_tid=cursor)
        assert scan["candidate"]["tid"] == expected_tid
        reasons.append(scan["candidate"]["reason"])
        assert scan["candidate"]["reason"] == expected_reason
        cursor = scan["cursor_after"]
    assert reasons == [
        "non_first_floor_has_images_without_asset",
        "image_count_asset_gap",
        "missing_or_pending_asset",
    ]


def test_auto_scheduler_ignores_downloaded_shared_image(db):
    db.execute(
        """INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
           VALUES (3435, 'raw', 'display', '2026-07-01T00:00:00+00:00', 2, 'complete', 5)"""
    )
    db.execute("INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (34351, 3435, 2, 'reply', 1)")
    db.execute(
        """INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, status)
           VALUES ('image-3435', 3435, 34351, 'image', 'https://example.invalid/a.webp', 'images/a.webp', 'downloaded'),
                  ('shared-3435', 3435, 34351, 'shared', 'https://example.invalid/static/image/smile.gif', 'shared/smile.gif', 'downloaded')"""
    )
    db.commit()

    scan = scheduler._select_candidate_batch(JobsRepository(db), _scheduler_settings(), dry_run=True, cursor_tid=0)
    assert scan["candidate"] is None
    assert scan["batch_size"] == 1


def test_auto_scheduler_batch_blocking_rules_match_single_tid_rules(db):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    for tid in (3440, 3450, 3460):
        _insert_gap_thread(db, tid)
    repo = JobsRepository(db)
    dry_job = repo.create("image_backfill", tid=3440, payload={"tid": 3440, "dry_run": True})
    repo.succeed(dry_job.job_id)
    apply_job = repo.create("image_backfill", tid=3450, payload={"tid": 3450, "dry_run": False})
    repo.succeed(apply_job.job_id)

    tids = [3440, 3450, 3460]
    for dry_run, expected in ((True, {3440, 3450}), (False, {3450})):
        batch_blocked = scheduler._blocking_backfill_tids(repo, tids, dry_run=dry_run)
        single_blocked = {
            tid for tid in tids
            if scheduler._has_blocking_backfill_job(repo, tid=tid, dry_run=dry_run)
        }
        assert batch_blocked == expected
        assert batch_blocked == single_blocked


def test_auto_scheduler_two_threads_enter_candidate_scan_once(monkeypatch):
    from yamibo_mcp.daemon import image_backfill_scheduler as scheduler

    class FakeTransaction:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConnection:
        backend = "sqlite"

        def commit(self):
            return None

        def begin(self):
            return FakeTransaction()

        def execute(self, *args, **kwargs):
            class Result:
                def fetchall(self):
                    return []

            return Result()

    class FakeStateRepository:
        def __init__(self, conn):
            self.conn = conn

        def get_json(self, key):
            return {}

    entered = threading.Event()
    release = threading.Event()
    scan_calls = []

    def fake_select(*args, **kwargs):
        scan_calls.append(threading.current_thread().name)
        entered.set()
        assert release.wait(timeout=2), "first scheduler scan did not release"
        return {
            "candidate": None,
            "batch_size": 0,
            "candidate_count": 0,
            "blocking_count": 0,
            "cursor_after": 0,
            "wrap_count": 0,
            "elapsed_ms": 0.0,
        }

    monkeypatch.setattr(scheduler, "SystemStateRepository", FakeStateRepository)
    monkeypatch.setattr(scheduler, "_select_candidate_batch", fake_select)
    settings = _scheduler_settings()
    results = []

    def invoke(name):
        repo = SimpleNamespace(conn=FakeConnection())
        results.append((name, maybe_enqueue_image_backfill_dry_run(repo, settings)))

    first = threading.Thread(target=invoke, args=("first",), name="scheduler-first")
    first.start()
    assert entered.wait(timeout=2), "first scheduler did not enter candidate scan"
    second = threading.Thread(target=invoke, args=("second",), name="scheduler-second")
    second.start()
    second.join(timeout=2)
    release.set()
    first.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(scan_calls) == 1
    assert sorted(results) == [("first", False), ("second", False)]


def test_v2_575256_slot_24_attachment_reconciles_without_remote(tmp_path, db, monkeypatch):
    tid = 575256
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTY0MDQzMnwxNzM%3D&nothumb=yes"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTY0MDQzMnwyNzQ%3D&nothumb=yes"
    paths = StoragePaths(tmp_path)
    image = paths.thread_images_dir(tid) / "floor_001_24.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    image.write_bytes(png)
    paths.thread_metadata(tid).parent.mkdir(parents=True, exist_ok=True)
    paths.thread_metadata(tid).write_text(json.dumps({"floors": [{
        "pid": 41608582, "floor_no": 1, "remote_image_urls": [current],
        "image_slots": [{"remote_url": current, "local_path": None, "status": "missing"}],
    }], "archive_status": "partial", "missing_image_urls": [current]}), encoding="utf-8")
    db.execute("INSERT INTO threads (tid, raw_title, display_title, image_count, archive_status, missing_images_json) VALUES (?, 'x', 'x', 1, 'partial', ?)", (tid, json.dumps([current])))
    db.execute("INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, status) VALUES ('1640432', ?, 41608582, 'attachment', ?, 'images/floor_001_24.jpg', 1, 'missing')", (tid, old))
    db.commit()

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.download_images_to_staging", lambda *args, **kwargs: pytest.fail("local reconcile must not download"))
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", lambda *args, **kwargs: pytest.fail("local reconcile must not fetch"))
    reconciled = _reconcile_missing_targets(db, paths=paths, tid=tid, target_urls=[current])

    assert reconciled == {current}
    row = db.execute("SELECT remote_url, local_path, status FROM assets WHERE asset_id = '1640432'").fetchone()
    assert (row["remote_url"], row["local_path"], row["status"]) == (current, "images/floor_001_24.jpg", "downloaded")
    metadata = json.loads(paths.thread_metadata(tid).read_text(encoding="utf-8"))
    assert metadata["floors"][0]["image_slots"] == [{"remote_url": current, "local_path": "images/floor_001_24.jpg", "status": "content"}]
    assert metadata["missing_image_urls"] == []
    assert metadata["archive_status"] == "complete"


def test_v2_missing_slot_24_fallback_preserves_original_target_position(tmp_path):
    from dataclasses import replace

    tid = 575256
    old = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTY0MDQzMnwxNzM%3D&nothumb=yes"
    current = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTY0MDQzMnwyNzQ%3D&nothumb=yes"
    source = tmp_path / "slot24.png"
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    source.write_bytes(png)
    target_url = source.as_uri()
    base = _snapshot(tid, [])
    urls = [f"https://example.invalid/slot-{index}.jpg" for index in range(1, 24)] + [target_url]
    first = replace(base.floors[0], pid=41608582, floor_no=1, has_images=True, image_urls=urls)
    snapshot = replace(base, floors=[first], image_count=24)
    target_positions = {old: {"pid": 41608582, "floor_no": 1, "image_index": 24}}
    assert _resolve_selected_remote_urls(snapshot, [old], target_positions) == {}
    target_positions[current] = {"pid": 41608582, "floor_no": 1, "image_index": 24}
    rotated_snapshot = replace(first, image_urls=urls[:-1] + [current])
    assert _resolve_selected_remote_urls(replace(snapshot, floors=[rotated_snapshot]), [old], target_positions) == {old: current}
    missing = _snapshot_for_missing_images(snapshot, [{"pid": 41608582, "floor_no": 1, "image_index": 24, "url": target_url}])
    result = download_images_to_staging(StoragePaths(tmp_path), "image_backfill_slot24", missing, target_urls={target_url}, timeout=1)
    assert result.missing_urls == []
    assert result.relative_path_by_url[target_url] == "images/floor_001_24.png"
    assert (tmp_path / "staging/jobs/image_backfill_slot24/images/floor_001_24.png").exists()


@pytest.mark.parametrize("valid_file", [True, False])
def test_idle_backfill_checks_files_before_already_complete(db, tmp_path, monkeypatch, valid_file):
    tid = 901
    paths = StoragePaths(tmp_path)
    image = paths.thread_images_dir(tid) / "floor_001_01.png"
    image.parent.mkdir(parents=True)
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    image.write_bytes(png if valid_file else b"broken")
    db.execute("INSERT INTO threads (tid, raw_title, image_count, archive_status) VALUES (?, 'x', 1, 'complete')", (tid,))
    db.execute("INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, status) VALUES ('done', ?, 1, 'image', 'https://example.org/image.png', 'images/floor_001_01.png', 1, 'downloaded')", (tid,))
    db.commit()
    snapshot = _snapshot(tid, [])
    # An already satisfied image job need not repair unrelated floor numbering.
    snapshot = replace(snapshot, floors=[replace(snapshot.floors[0], pid=1, floor_no=2, has_images=True)])
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill._load_local_snapshot_for_backfill", lambda *args: snapshot)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", lambda *args, **kwargs: pytest.fail("unexpected remote fetch"))
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload={"mode": "reconcile_missing", "internal_auto": True, "dry_run": False})
    settings = SimpleNamespace(data_dir=tmp_path, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novels")
    if valid_file:
        handle_image_backfill(repo, job, "test", 60, settings)
        result = repo.get(job.job_id)
        assert result.status == "succeeded"
        assert result.artifacts["already_complete"] is True
        assert result.artifacts["remote_fetch"] is False
    else:
        with pytest.raises(ValueError, match="local floor sequence"):
            handle_image_backfill(repo, job, "test", 60, settings)


def test_auto_backfill_does_not_fail_selected_guard_for_external_only_target(db, tmp_path, monkeypatch):
    tid = 902
    external_url = "http://mis.im.tku.edu.tw/~fireflyyen19a/new/src/image.jpg"
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, image_count, archive_status, forum_id)
        VALUES (?, 'raw', 'display', 1, 'partial', 5)
        """,
        (tid,),
    )
    db.execute(
        "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, 2, 'reply', 1)",
        (tid * 10 + 2, tid),
    )
    db.commit()
    paths = StoragePaths(tmp_path)
    metadata_path = paths.thread_metadata(tid)
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(json.dumps({
        "floors": [{
            "remote_image_urls": [external_url],
            "image_slots": [{"remote_url": external_url, "local_path": None, "status": "missing"}],
        }],
        "missing_image_urls": [external_url],
    }), encoding="utf-8")

    snapshot = _snapshot(tid, [external_url])
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.image_backfill._load_local_snapshot_for_backfill",
        lambda *args: snapshot,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.image_backfill.select_thread_proxy",
        lambda *args, **kwargs: None,
    )

    class _Fetch:
        html = "<html></html>"
        final_url = f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}"

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = None

        def fetch_thread_page(self, **kwargs):
            return _Fetch()

    @contextmanager
    def _borrow(settings, **kwargs):
        yield SimpleNamespace(account_id="pool", permission_level=10), _Client()

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.image_backfill.parse_thread_snapshot",
        lambda *args, **kwargs: snapshot,
    )
    download_targets = []

    def _download(*args, **kwargs):
        download_targets.append(kwargs["target_urls"])
        return ImageDownloadResult()

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.download_images_to_staging", _download)

    repo = JobsRepository(db)
    job = repo.create(
        "image_backfill",
        tid=tid,
        payload={"tid": tid, "mode": "reconcile_missing", "internal_auto": True, "dry_run": False},
    )
    settings = SimpleNamespace(
        data_dir=tmp_path,
        export_dir=tmp_path / "exports",
        novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
        image_download_timeout_seconds=1.0,
        image_download_retries=0,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert completed.error_code is None
    assert completed.artifacts["already_complete"] is True
    assert completed.artifacts["download_skipped_reason"] == "no_missing_content_targets"
    assert download_targets == []


def test_v2_scheduler_accepts_first_floor_metadata_missing_candidate(db):
    tid = 575257
    db.execute("INSERT INTO threads (tid, raw_title, display_title, image_count, archive_status, forum_id, missing_images_json) VALUES (?, 'x', 'x', 1, 'complete', 5, ?)", (tid, json.dumps(["https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTY0MDQzM3wx&nothumb=yes"])))
    db.execute("INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, 1, 'first', 1)", (tid + 1, tid))
    db.commit()
    result = scheduler._select_candidate_batch(JobsRepository(db), _scheduler_settings(), dry_run=True, cursor_tid=0, campaign="metadata_reconcile_v1")
    assert result["candidate"]["tid"] == tid


def test_v2_foreground_checks_scan_all_rows_and_auto_is_not_foreground(db):
    repo = JobsRepository(db)
    auto = repo.create("image_backfill", tid=1, payload={"internal_auto": True, "campaign": "old"})
    assert scheduler._has_foreground_work(repo) is False
    assert _foreground_work_available(repo, auto.job_id) is False
    foreground = repo.create("image_backfill", tid=2, payload={"scope": "selected"})
    assert scheduler._has_foreground_work(repo) is True
    assert _foreground_work_available(repo, auto.job_id) is True
    assert _foreground_work_available(repo, foreground.job_id) is False
    repo.create("sync_thread", tid=3)
    assert _foreground_work_available(repo, foreground.job_id) is True


def test_cancel_requested_foreground_jobs_do_not_block_idle_image_backfill(db):
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id)
        VALUES (9001, 'raw', 'display', '2026-07-01T00:00:00+00:00', 1, 'complete', 5)
        """
    )
    db.execute(
        "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (90011, 9001, 2, 'reply', 1)"
    )
    repo = JobsRepository(db)
    daily = repo.create("daily_sign_in", tid=20260912, payload={"account_id": "a"})
    db.execute("UPDATE jobs SET status = 'cancel_requested' WHERE job_id = ?", (daily.job_id,))
    db.commit()

    assert scheduler._has_foreground_work(repo) is False
    assert maybe_enqueue_image_backfill_dry_run(repo, _scheduler_settings()) is True
    auto_row = db.execute(
        "SELECT job_id FROM jobs WHERE job_type = 'image_backfill' AND tid = 9001"
    ).fetchone()
    assert auto_row is not None
    auto = repo.get(auto_row["job_id"])
    assert _foreground_work_available(repo, auto.job_id) is False


def test_v2_any_live_auto_campaign_blocks_but_terminal_old_campaign_does_not(db):
    repo = JobsRepository(db)
    old = repo.create("image_backfill", tid=10, payload={"internal_auto": True, "campaign": "old"})
    assert scheduler._has_live_automatic_job(repo, campaign="metadata_reconcile_v1", fingerprint="new") is True
    repo.succeed(old.job_id)
    assert scheduler._has_live_automatic_job(repo, campaign="metadata_reconcile_v1", fingerprint="new") is False


def test_campaign_blocks_same_campaign_failed_job(db):
    repo = JobsRepository(db)
    job = repo.create(
        "image_backfill",
        tid=777,
        payload={"internal_auto": True, "campaign": "metadata_reconcile_v1", "dry_run": False},
    )
    db.execute(
        "UPDATE jobs SET status='failed', error_code='INVALID_ARGUMENT' WHERE job_id=?",
        (job.job_id,),
    )
    db.commit()
    assert scheduler._blocking_backfill_tids(
        repo, [777], dry_run=False, campaign="metadata_reconcile_v1"
    ) == {777}


@pytest.mark.parametrize("automatic", [False, True])
def test_image_backfill_uses_saved_url_without_fetching_thread(db, tmp_path, monkeypatch, automatic):
    tid = 2401 if automatic else 2400
    url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/missing.png"
    existing_url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/existing.png"
    snapshot = _snapshot(tid, [url, existing_url] if automatic else [url])
    paths = StoragePaths(tmp_path)
    materialize_thread(paths, snapshot, missing_image_urls=[url])
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    db.execute(
        """INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id, missing_images_json)
           VALUES (?, 'title', 'title', '2026-07-01T00:00:00+00:00', ?, 'partial', 5, ?)""",
        (tid, len(snapshot.floors[1].image_urls), json.dumps([url])),
    )
    for floor in snapshot.floors:
        db.execute(
            "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, ?, ?, ?)",
            (floor.pid, tid, floor.floor_no, floor.content, bool(floor.image_urls)),
        )
    if automatic:
        existing_path = paths.thread_images_dir(tid) / "floor_002_02.png"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_bytes(png)
        db.execute(
            """INSERT INTO assets
               (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, required, status)
               VALUES ('already-downloaded', ?, ?, 'image', ?, 'images/floor_002_02.png', 1, 1, 'downloaded')""",
            (tid, snapshot.floors[1].pid, existing_url),
        )
    else:
        db.execute(
            """INSERT INTO assets
               (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, required, status)
               VALUES ('manual-missing', ?, ?, 'image', ?, NULL, 1, 1, 'missing')""",
            (tid, snapshot.floors[1].pid, url),
        )
    db.commit()

    image_calls = []

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = None

        def fetch_image(self, image_url, **kwargs):
            image_calls.append(image_url)
            return SimpleNamespace(status_code=200, final_url=image_url, headers={"Content-Type": "image/png"}, content=bytes(png))

        def fetch_thread_page(self, **kwargs):
            pytest.fail("a valid saved image URL must not fetch the thread")

    @contextmanager
    def _borrow(settings, **kwargs):
        yield SimpleNamespace(account_id="test", permission_level=10), _Client()

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.select_thread_proxy", lambda *args, **kwargs: None)
    payload = {"tid": tid, "dry_run": False}
    if automatic:
        payload.update(mode="reconcile_missing", scope="selected", internal_auto=True)
    else:
        payload.update(scope="selected", target_urls=[url], target_asset_id="manual-missing")
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload=payload)
    settings = SimpleNamespace(
        data_dir=tmp_path, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=50, image_backfill_fixed_after=None,
        image_download_timeout_seconds=1.0, image_download_retries=0,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert completed.artifacts["pages_fetched"] == 0
    assert completed.artifacts["remote_fetch"] is False
    assert image_calls == [url]
    assert paths.thread_images_dir(tid).joinpath("floor_002_01.png").exists()
    asset = db.execute("SELECT local_path, status FROM assets WHERE tid = ? AND remote_url = ?", (tid, url)).fetchone()
    assert asset["local_path"] == "images/floor_002_01.png"
    assert asset["status"] == "downloaded"
    thread = db.execute("SELECT archive_status, missing_images_json, sync_time FROM threads WHERE tid = ?", (tid,)).fetchone()
    assert thread["archive_status"] == "complete"
    assert json.loads(thread["missing_images_json"]) == []
    assert str(thread["sync_time"]).startswith("2026-07-01")


def test_text_only_upgrade_downloads_saved_images_from_first_and_later_floors(db, tmp_path, monkeypatch):
    tid = 2490
    first_url = "https://bbs.yamibo.com/data/attachment/forum/first.png"
    reply_url = "https://bbs.yamibo.com/data/attachment/forum/reply.png"
    base = _snapshot(tid, [reply_url])
    first = replace(base.floors[0], has_images=True, image_urls=[first_url])
    snapshot = replace(base, floors=[first, base.floors[1]], image_count=2)
    paths = StoragePaths(tmp_path)
    materialize_thread(paths, snapshot)
    db.execute(
        """INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, capture_mode, forum_id)
           VALUES (?, 'title', 'title', '2026-07-01T00:00:00+00:00', 2, 'complete', 'text_only', 30)""",
        (tid,),
    )
    for floor in snapshot.floors:
        db.execute(
            "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, ?, ?, 1)",
            (floor.pid, tid, floor.floor_no, floor.content),
        )
        db.execute(
            """INSERT INTO assets (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, required, status)
               VALUES (?, ?, ?, 'image', ?, NULL, 1, 1, 'pending')""",
            (f"asset-{floor.pid}", tid, floor.pid, floor.image_urls[0]),
        )
    db.commit()

    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    image_calls = []

    class Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = None

        def fetch_image(self, image_url, **kwargs):
            image_calls.append(image_url)
            return SimpleNamespace(status_code=200, final_url=image_url, headers={"Content-Type": "image/png"}, content=bytes(png))

        def fetch_thread_page(self, **kwargs):
            pytest.fail("upgrading saved images must not fetch the thread")

    @contextmanager
    def borrow(settings, **kwargs):
        yield SimpleNamespace(account_id="test", permission_level=10), Client()

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", borrow)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.select_thread_proxy", lambda *a, **k: None)
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload={"tid": tid, "upgrade_to_full": True})
    settings = SimpleNamespace(
        data_dir=tmp_path, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1, image_backfill_fixed_after=None,
        image_download_timeout_seconds=1.0, image_download_retries=0,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    assert repo.get(job.job_id).status == "succeeded"
    assert set(image_calls) == {first_url, reply_url}
    row = db.execute("SELECT capture_mode, archive_status FROM threads WHERE tid = ?", (tid,)).fetchone()
    assert (row["capture_mode"], row["archive_status"]) == ("full", "complete")
    assets = db.execute("SELECT local_path FROM assets WHERE tid = ?", (tid,)).fetchall()
    assert len(assets) == 2 and all(asset["local_path"] for asset in assets)


@pytest.mark.parametrize(
    ("old_url", "new_url"),
    [
        (
            "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHxvbGQ%3D&nothumb=yes",
            "https://bbs.yamibo.com/forum.php?mod=attachment&aid=MTYzODQzNHxuZXc%3D&nothumb=yes",
        ),
        (
            "https://bbs.yamibo.com/data/attachment/album/201506/12/old-thumb.jpg",
            "https://bbs.yamibo.com/data/attachment/album/201506/12/new-image.png",
        ),
    ],
)
def test_image_backfill_refreshes_expired_attachment_url_after_direct_failure(db, tmp_path, monkeypatch, old_url, new_url):
    tid = 2402
    local_snapshot = _snapshot(tid, [old_url])
    paths = StoragePaths(tmp_path)
    materialize_thread(paths, local_snapshot, missing_image_urls=[old_url])
    db.execute(
        """INSERT INTO threads (tid, raw_title, display_title, image_count, archive_status, forum_id, missing_images_json)
           VALUES (?, 'title', 'title', 1, 'partial', 5, ?)""",
        (tid, json.dumps([old_url])),
    )
    for floor in local_snapshot.floors:
        db.execute(
            "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, ?, ?, ?)",
            (floor.pid, tid, floor.floor_no, floor.content, bool(floor.image_urls)),
        )
    db.execute(
        """INSERT INTO assets
           (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, required, status)
           VALUES ('expired-attachment', ?, ?, 'attachment', ?, NULL, 1, 1, 'missing')""",
        (tid, local_snapshot.floors[1].pid, old_url),
    )
    db.commit()
    png = bytearray(b"\x89PNG\r\n\x1a\n" + b"\x00" * 108 + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    png[16:20] = (640).to_bytes(4, "big")
    png[20:24] = (480).to_bytes(4, "big")
    calls = []

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = None

        def fetch_image(self, url, **kwargs):
            calls.append(("image", url))
            return SimpleNamespace(
                status_code=404 if url == old_url else 200,
                final_url=url,
                headers={"Content-Type": "image/png"},
                content=b"missing" if url == old_url else bytes(png),
            )

        def fetch_thread_page(self, **kwargs):
            calls.append(("thread", tid))
            return SimpleNamespace(html="<html></html>", final_url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}")

    @contextmanager
    def _borrow(settings, **kwargs):
        yield SimpleNamespace(account_id="test", permission_level=10), _Client()

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.parse_thread_snapshot", lambda *args, **kwargs: _snapshot(tid, [new_url]))
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload={
        "tid": tid, "dry_run": False, "scope": "selected", "target_urls": [old_url],
        "target_asset_id": "expired-attachment", "max_pages": 1,
    })
    settings = SimpleNamespace(
        data_dir=tmp_path, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1, image_backfill_fixed_after=None,
        image_download_timeout_seconds=1.0, image_download_retries=0,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert completed.artifacts["pages_fetched"] == 1
    assert calls == [("image", old_url), ("thread", tid), ("image", new_url)]
    asset = db.execute("SELECT remote_url, local_path FROM assets WHERE tid = ?", (tid,)).fetchone()
    assert asset["remote_url"] == new_url
    assert asset["local_path"] == "images/floor_002_01.png"


def test_image_backfill_apply_downloads_and_persists_missing_images(db, tmp_path, monkeypatch):
    tid = 2002
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id, context_path)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 0, 'complete', 5, ?)
        """,
        (tid, f"threads/{tid}/context.md"),
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (?, ?, 1, 'first', 0)
        """,
        (tid * 10 + 1, tid),
    )
    db.commit()
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload={"tid": tid, "dry_run": False, "max_pages": 1})

    content_url = "https://bbs.yamibo.com/data/attachment/forum/202501/01/image.jpg"
    static_url = "https://bbs.yamibo.com/static/image/smiley/gexing/008.gif"
    snapshot = _snapshot(tid, [content_url, static_url])

    class _Fetch:
        html = "<html></html>"
        final_url = f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}"

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = "http://127.0.0.1:9999"

        def fetch_thread_page(self, **kwargs):
            return _Fetch()

    borrow_kwargs = []
    download_kwargs = []

    @contextmanager
    def _borrow(settings, **kwargs):
        borrow_kwargs.append(kwargs)
        yield SimpleNamespace(account_id="pool", permission_level=10), _Client()

    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.image_backfill.select_thread_proxy",
        lambda *args, **kwargs: SimpleNamespace(
            proxy_url="http://127.0.0.1:9999",
            group="archive",
            node="n1",
            best_effort=False,
            diagnostics={},
        ),
    )
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.parse_thread_snapshot", lambda *args, **kwargs: snapshot)

    def _download(*args, **kwargs):
        download_kwargs.append(kwargs)
        return ImageDownloadResult(
            downloaded_relpaths={tid * 10 + 2: ["images/floor_002_01.jpg"]},
            shared_relpaths={tid * 10 + 2: ["shared/bbs.yamibo.com/static/image/smiley/gexing/008.gif"]},
            downloaded_count=1,
            shared_downloaded_count=1,
            diagnostics=[{
                "url": content_url,
                "final_url": content_url,
                "content_type": "image/jpeg",
                "http_status": 200,
                "bytes": 1024,
                "attempts": 1,
                "duration_ms": 12.5,
                "transport": "urllib",
                "status": "ok",
                "error_type": None,
                "error_message": None,
                "retryable": None,
            }],
        )

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.download_images_to_staging", _download)

    settings = SimpleNamespace(
        data_dir=tmp_path,
        export_dir=tmp_path / "exports",
        novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
        image_download_timeout_seconds=1.0,
        image_download_retries=0,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert completed.artifacts["dry_run"] is False
    assert completed.artifacts["backfilled_image_count"] == 2
    assert borrow_kwargs == [{"min_permission": None, "proxy_url": "http://127.0.0.1:9999", "force_direct": False}]
    assert download_kwargs[0]["proxy_url"] == "http://127.0.0.1:9999"
    assert completed.artifacts["proxy_pool.enabled"] is True
    assert completed.artifacts["proxy_pool.node"] == "n1"
    assert completed.artifacts["image_download_summary"]["attempted"] == 1
    assert completed.artifacts["image_download_summary"]["succeeded"] == 1
    assert completed.artifacts["image_download_diagnostics"][0]["url_host"] == "bbs.yamibo.com"
    assert "url" not in completed.artifacts["image_download_diagnostics"][0]
    events = JobEventsRepository(db).list(job_id=job.job_id, limit=100)
    image_events = [event for event in events if event.event_type == "image.download.result"]
    assert len(image_events) == 1
    assert image_events[0].payload["summary"]["failed"] == 0
    assets = db.execute("SELECT remote_url, local_path, status FROM assets WHERE tid = ? ORDER BY remote_url", (tid,)).fetchall()
    assert {row["remote_url"]: row["local_path"] for row in assets} == {
        content_url: "images/floor_002_01.jpg",
        static_url: "shared/bbs.yamibo.com/static/image/smiley/gexing/008.gif",
    }
    assert {row["status"] for row in assets} == {"downloaded"}


def test_interactive_image_backfill_prefers_direct_then_falls_back_to_proxy(db, tmp_path, monkeypatch):
    tid = 2004
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id, context_path)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 0, 'complete', 5, ?)
        """,
        (tid, f"threads/{tid}/context.md"),
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (?, ?, 1, 'first', 0)
        """,
        (tid * 10 + 1, tid),
    )
    db.commit()
    repo = JobsRepository(db)
    job = repo.create(
        "image_backfill",
        tid=tid,
        payload={"tid": tid, "dry_run": True, "max_pages": 1, "priority": "interactive"},
    )

    class _Fetch:
        html = "<html></html>"
        final_url = f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}"

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False

        def __init__(self, proxy_url):
            self.proxy_url = proxy_url

        def fetch_thread_page(self, **kwargs):
            if self.proxy_url is None:
                raise RemoteFetchError(
                    "soft block detected",
                    details={"status_code": 403, "retryable": True},
                )
            return _Fetch()

    borrow_kwargs = []

    @contextmanager
    def _borrow(settings, **kwargs):
        borrow_kwargs.append(kwargs)
        account_id = f"account-{len(borrow_kwargs)}"
        yield SimpleNamespace(account_id=account_id, permission_level=10), _Client(kwargs.get("proxy_url"))

    selected_proxy_calls = []
    proxy_binding = SimpleNamespace(
        proxy_url="http://127.0.0.1:9999",
        group="archive",
        node="node-proxy",
        best_effort=False,
        diagnostics={"retry_hint": 1, "candidate_tier": "A"},
    )

    def _select_proxy(*args, **kwargs):
        selected_proxy_calls.append(kwargs)
        return proxy_binding

    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.image_backfill.select_thread_proxy",
        _select_proxy,
    )
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.image_backfill.parse_thread_snapshot",
        lambda *args, **kwargs: _snapshot(tid, []),
    )

    settings = SimpleNamespace(
        data_dir=tmp_path,
        export_dir=tmp_path / "exports",
        novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
        use_system_proxy=True,
        proxy_pool=SimpleNamespace(enabled=True),
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert [kwargs["force_direct"] for kwargs in borrow_kwargs] == [True, False]
    assert borrow_kwargs[0]["proxy_url"] is None
    assert borrow_kwargs[1]["proxy_url"] == "http://127.0.0.1:9999"
    assert borrow_kwargs[1]["exclude_account_ids"] == {"account-1"}
    assert selected_proxy_calls[0]["exclude_nodes"] == {"DIRECT"}
    assert completed.artifacts["remote_transport"] == "proxy"
    assert completed.artifacts["direct_fallback_error"] == "soft block detected"
    attempt = completed.artifacts["remote_attempt"]
    assert attempt["nodes_tried"] == ["DIRECT", "node-proxy"]
    assert attempt["account_ids_tried"] == ["account-1", "account-2"]


def test_image_backfill_reborrows_with_higher_permission_after_permission_gate(db, tmp_path, monkeypatch):
    tid = 2003
    db.execute(
        """
        INSERT INTO threads (tid, raw_title, display_title, sync_time, image_count, archive_status, forum_id, context_path)
        VALUES (?, 'raw', 'display', '2026-07-01T00:00:00+00:00', 0, 'complete', 5, ?)
        """,
        (tid, f"threads/{tid}/context.md"),
    )
    db.execute(
        """
        INSERT INTO floors (pid, tid, floor_no, content, has_images)
        VALUES (?, ?, 1, 'first', 0)
        """,
        (tid * 10 + 1, tid),
    )
    db.commit()
    repo = JobsRepository(db)
    job = repo.create("image_backfill", tid=tid, payload={"tid": tid, "dry_run": True, "max_pages": 1})

    class _Fetch:
        html = "<html></html>"
        final_url = f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}"

    class _Client:
        headers = {}
        cookie_jar = None
        cookie_file = None
        use_system_proxy = False
        proxy_url = None

        def __init__(self, *, fail_permission: bool):
            self.fail_permission = fail_permission

        def fetch_thread_page(self, **kwargs):
            if self.fail_permission:
                raise ThreadPermissionRequiredError("requires permission", required_permission=50)
            return _Fetch()

    borrowed_min_permissions = []

    @contextmanager
    def _borrow(settings, **kwargs):
        borrowed_min_permissions.append(kwargs.get("min_permission"))
        if kwargs.get("min_permission") is None:
            yield SimpleNamespace(account_id="low", permission_level=10), _Client(fail_permission=True)
        else:
            yield SimpleNamespace(account_id="high", permission_level=50), _Client(fail_permission=False)

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.borrow_yamibo_client", _borrow)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.image_backfill.parse_thread_snapshot", lambda *args, **kwargs: _snapshot(tid, []))

    settings = SimpleNamespace(
        data_dir=tmp_path,
        export_dir=tmp_path / "exports",
        novel_txt_export_dir=tmp_path / "novel_exports",
        image_backfill_max_pages=1,
        image_backfill_fixed_after=None,
        proxy_pool=None,
    )

    handle_image_backfill(repo, repo.get(job.job_id), "worker", 60, settings)

    completed = repo.get(job.job_id)
    assert completed.status == "succeeded"
    assert borrowed_min_permissions == [None, 50]
    assert completed.artifacts["account_id"] == "high"
    assert completed.artifacts["account_permission_level"] == 50
    assert completed.artifacts["account_min_permission"] == 50


def test_auto_scheduler_excludes_intentional_text_only_archives(db):
    for tid, mode in ((9901, "text_only"), (9902, "full")):
        db.execute(
            """INSERT INTO threads (tid, raw_title, archive_status, forum_id, image_count, capture_mode)
               VALUES (?, 'test', 'complete', 5, 1, ?)""", (tid, mode),
        )
        db.execute(
            "INSERT INTO floors (pid, tid, floor_no, content, has_images) VALUES (?, ?, 2, 'reply', 1)",
            (tid * 10, tid),
        )
    db.commit()
    scan = scheduler._select_candidate_batch(
        JobsRepository(db), _scheduler_settings(), dry_run=True, cursor_tid=0,
    )
    assert scan["candidate"]["tid"] == 9902
    assert scan["candidate_count"] == 1


def test_auto_scheduler_revalidates_mode_of_cached_candidate(db):
    db.execute(
        "INSERT INTO threads (tid, raw_title, capture_mode) VALUES (9911, 'test', 'text_only')"
    )
    db.execute(
        "INSERT INTO system_state (key, value_json, updated_at) VALUES (?, ?, ?)",
        ("image_backfill_auto_scheduler", json.dumps({
            "scan_forum_id": 5,
            "pending_forum_id": 5,
            "pending_dry_run": True,
            "pending_candidates": [{"tid": 9911, "reason": "old_batch"}],
        }), "2026-09-24T00:00:00+00:00"),
    )
    db.commit()
    assert maybe_enqueue_image_backfill_dry_run(JobsRepository(db), _scheduler_settings()) is False
    assert db.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 0
    state = SystemStateRepository(db).get_json("image_backfill_auto_scheduler")
    assert state["pending_candidates"] == []
