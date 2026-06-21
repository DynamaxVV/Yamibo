from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from yamibo_mcp.daemon.handlers.sync_thread import handle_sync_thread
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.images import ImageDownloadResult


def _make_settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    export_dir = tmp_path / "exports"
    novel_export_dir = tmp_path / "novel_exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    novel_export_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("", encoding="utf-8")
    return SimpleNamespace(
        data_dir=data_dir,
        export_dir=export_dir,
        novel_txt_export_dir=novel_export_dir,
        project_root=tmp_path,
        cookie_file=cookie_file,
        use_system_proxy=False,
        login_username=None,
        login_password=None,
        image_download_timeout_seconds=5,
        image_download_retries=0,
        worker_lease_seconds=300,
        novel_author_only_max_pages=5,
        novel_author_only_page_delay_seconds=0.0,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        request_interval_seconds=0.0,
        request_interval_jitter_seconds=0.0,
    )


def _make_snapshot() -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="[组] 测试帖子",
        display_title="测试帖子",
        group_name="组",
        author_guess="作者",
        core_title_guess="测试帖子",
        normalized_core_title="测试帖子",
        series_key="测试帖子",
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
    floor = FloorSnapshot(
        pid=1001,
        tid=42,
        floor_no=1,
        publisher="u1",
        content="正文",
        pub_time="2025-01-01 00:00",
        has_images=True,
        image_urls=["https://img.example.com/a.jpg"],
    )
    return ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="u1",
        publisher_uid="100",
        pub_time="2025-01-01 00:00",
        permission=0,
        floors=[floor],
        image_count=1,
    )


def test_sync_thread_partial_updates_forum_blocks_assets_and_events(db, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    html_path = tmp_path / "thread.html"
    html_path.write_text("<html></html>", encoding="utf-8")
    repo = JobsRepository(db)
    job = repo.create(
        "sync_thread",
        tid=42,
        payload={"html_path": str(html_path), "forum_id": 55},
    )
    job = repo.acquire(job.job_id, "worker-1", 300)
    snapshot = _make_snapshot()

    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.parse_thread_snapshot",
        lambda html, url=None, tid=None: snapshot,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.refine_title_parse_with_llm",
        lambda settings, raw_title, parsed: (parsed, None),
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.write_staging_title_parse_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.write_staging_snapshot",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.update_title_hints",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.materialize_thread",
        lambda *args, **kwargs: (
            settings.data_dir / "threads/42/context.md",
            settings.data_dir / "threads/42/metadata.json",
        ),
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.download_images_to_staging",
        lambda *args, **kwargs: ImageDownloadResult(
            downloaded_relpaths={},
            non_export_relpaths={},
            shared_relpaths={},
            skipped_relpaths={},
            downloaded_count=0,
            non_export_count=0,
            shared_downloaded_count=0,
            missing_urls=["https://img.example.com/a.jpg"],
            missing_shared_urls=[],
        ),
    )

    handle_sync_thread(repo, job, "worker-1", 300, settings)

    thread_row = ThreadsRepository(db).get_thread(42)
    block_rows = ContentBlocksRepository(db).list_blocks(42)
    asset_rows = AssetsRepository(db).list_assets(42)
    events = JobEventsRepository(db).list(job_id=job.job_id)

    assert thread_row is not None
    assert thread_row["forum_id"] == 55
    assert thread_row["content_kind"] == "novel"
    assert len(block_rows) >= 2
    assert len(asset_rows) == 1
    assert asset_rows[0]["status"] == "missing"
    assert any(event.event_type == "job.partial" for event in events)


def test_sync_thread_author_only_mode_merges_multiple_pages(db, tmp_path, monkeypatch):
    from yamibo_mcp.daemon.handlers.sync_thread import _merge_thread_snapshots

    snap1 = _make_snapshot()
    snap2 = ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=2&authorid=100",
        page_type="thread_detail",
        raw_title=snap1.raw_title,
        display_title=snap1.display_title,
        title=snap1.title,
        publisher=snap1.publisher,
        publisher_uid=snap1.publisher_uid,
        pub_time=snap1.pub_time,
        permission=0,
        floors=[
            FloorSnapshot(
                pid=1002,
                tid=42,
                floor_no=1,
                publisher="u1",
                content="第二页正文",
                pub_time="2025-01-02 00:00",
                has_images=False,
                image_urls=[],
            )
        ],
        image_count=0,
    )
    merged = _merge_thread_snapshots([snap1, snap2])
    assert [floor.pid for floor in merged.floors] == [1001, 1002]
    assert [floor.floor_no for floor in merged.floors] == [1, 2]
    assert "authorid=100" in str(merged.url)
