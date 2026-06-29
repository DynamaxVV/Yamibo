from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

from yamibo_mcp.errors import ThreadPermissionRequiredError
from yamibo_mcp.daemon.handlers.sync_thread import handle_sync_thread
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.images import ImageDownloadResult
from yamibo_mcp.yamibo.client import FetchResult


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
        archive_thread_max_pages=50,
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


def test_sync_thread_retries_permission_gate_with_next_threshold(db, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    repo = JobsRepository(db)
    job = repo.create(
        "sync_thread",
        tid=42,
        payload={"tid": 42, "forum_id": 55},
    )
    job = repo.acquire(job.job_id, "worker-1", 300)
    snapshot = replace(
        _make_snapshot(),
        image_count=0,
        floors=[replace(_make_snapshot().floors[0], has_images=False, image_urls=[])],
    )

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
        lambda *args, **kwargs: ImageDownloadResult(),
    )

    calls: list[int | None] = []

    class _BorrowContext:
        def __init__(self, fail: bool) -> None:
            self.fail = fail

        def __enter__(self):
            if self.fail:
                raise ThreadPermissionRequiredError(
                    "thread requires read permission above 10 for https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42",
                    required_permission=10,
                )
            client = SimpleNamespace(
                headers={},
                cookie_jar=None,
                use_system_proxy=False,
                cookie_file=None,
                fetch_thread=lambda **kwargs: FetchResult(
                    url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42",
                    final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42",
                    status_code=200,
                    html="<html><body><div id='post_1'><td id='postmessage_1'>正文</td></div></body></html>",
                ),
                fetch_author_only_thread_pages=lambda **kwargs: (
                    [
                        FetchResult(
                            url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=1",
                            final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=1",
                            status_code=200,
                            html="<html><body><div id='post_1'><td id='postmessage_1'>正文</td></div></body></html>",
                        )
                    ],
                    1,
                    "done",
                ),
                fetch_thread_pages=lambda **kwargs: (
                    [
                        FetchResult(
                            url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=1",
                            final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=1",
                            status_code=200,
                            html="<html><body><div id='post_1'><td id='postmessage_1'>正文</td></div></body></html>",
                        )
                    ],
                    1,
                    "done",
                ),
            )
            return SimpleNamespace(account_id="high"), client

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_borrow(settings_arg, *, min_permission=None, prefer_high_permission=False, cookie_file=None):
        calls.append(min_permission)
        return _BorrowContext(fail=len(calls) == 1)

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.has_configured_account_pool", lambda settings: True)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.borrow_yamibo_client", fake_borrow)

    handle_sync_thread(repo, job, "worker-1", 300, settings)

    assert calls == [None, 11]


def test_sync_thread_fails_empty_primary_floor_without_images(db, tmp_path, monkeypatch):
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
    snapshot = replace(
        snapshot,
        floors=[replace(snapshot.floors[0], content="", has_images=False, image_urls=[])],
        image_count=0,
    )

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
            missing_urls=[],
            missing_shared_urls=[],
        ),
    )

    try:
        handle_sync_thread(repo, job, "worker-1", 300, settings)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "content is required when no images are present" in str(exc)

    failure_path = settings.data_dir / "staging" / "jobs" / job.job_id / "failure.json"
    assert failure_path.exists()
    assert "content is required when no images are present" in failure_path.read_text(encoding="utf-8")
    assert ThreadsRepository(db).get_thread(42) is None


def test_sync_thread_partial_on_download_timeout(db, tmp_path, monkeypatch):
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
            downloaded_relpaths={1: ["images/a.jpg"]},
            non_export_relpaths={},
            shared_relpaths={},
            skipped_relpaths={},
            downloaded_count=1,
            non_export_count=0,
            shared_downloaded_count=0,
            missing_urls=[],
            missing_shared_urls=[],
            stopped_reason="stage_timeout",
        ),
    )

    handle_sync_thread(repo, job, "worker-1", 300, settings)

    updated_job = repo.get(job.job_id)
    events = JobEventsRepository(db).list(job_id=job.job_id)

    assert updated_job.status == "partial"
    assert updated_job.artifacts["download_stopped_reason"] == "stage_timeout"
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


def test_sync_thread_fetches_multiple_pages_for_non_novel_threads(db, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    settings.archive_thread_max_pages = 50
    repo = JobsRepository(db)
    job = repo.create("sync_thread", tid=42, payload={"tid": 42, "forum_id": 30})
    job = repo.acquire(job.job_id, "worker-1", 300)

    page1 = _make_snapshot()
    page2 = ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=2",
        page_type="thread_detail",
        raw_title=page1.raw_title,
        display_title=page1.display_title,
        title=page1.title,
        publisher=page1.publisher,
        publisher_uid=page1.publisher_uid,
        pub_time=page1.pub_time,
        permission=0,
        floors=[
            FloorSnapshot(
                pid=1002,
                tid=42,
                floor_no=1,
                publisher="u2",
                content="第二页正文",
                pub_time="2025-01-02 00:00",
                has_images=False,
                image_urls=[],
            )
        ],
        image_count=0,
    )
    page3 = ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=3",
        page_type="thread_detail",
        raw_title=page1.raw_title,
        display_title=page1.display_title,
        title=page1.title,
        publisher=page1.publisher,
        publisher_uid=page1.publisher_uid,
        pub_time=page1.pub_time,
        permission=0,
        floors=[
            FloorSnapshot(
                pid=1003,
                tid=42,
                floor_no=1,
                publisher="u3",
                content="第三页正文",
                pub_time="2025-01-03 00:00",
                has_images=False,
                image_urls=[],
            )
        ],
        image_count=0,
    )
    snapshots_by_url = {snap.url: snap for snap in (page1, page2, page3)}

    class _FakeClient:
        headers = {}
        cookie_jar = None
        use_system_proxy = False

        def fetch_thread(self, **kwargs):
            return SimpleNamespace(html="page-1", final_url=page1.url)

        def fetch_thread_pages(self, **kwargs):
            assert kwargs["tid"] == 42
            assert kwargs["max_pages"] == 50
            return (
                [
                    SimpleNamespace(html="page-1", final_url=page1.url),
                    SimpleNamespace(html="page-2", final_url=page2.url),
                    SimpleNamespace(html="page-3", final_url=page3.url),
                ],
                3,
                "last_page",
            )

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.YamiboClient", lambda **kwargs: _FakeClient())
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.parse_thread_snapshot",
        lambda html, url=None, tid=None: snapshots_by_url[url],
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.refine_title_parse_with_llm",
        lambda settings, raw_title, parsed: (parsed, None),
    )
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.write_staging_title_parse_log", lambda *args, **kwargs: None)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.write_staging_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.update_title_hints", lambda *args, **kwargs: None)
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
            missing_urls=[],
            missing_shared_urls=[],
        ),
    )

    handle_sync_thread(repo, job, "worker-1", 300, settings)

    thread_row = ThreadsRepository(db).get_thread(42)
    finished_job = repo.get(job.job_id)

    assert thread_row is not None
    assert thread_row["forum_id"] == 30
    assert [row["pid"] for row in ThreadsRepository(db).list_floors(42)] == [1001, 1002, 1003]
    assert finished_job.artifacts["pages_fetched"] == 3
    assert finished_job.artifacts["stopped_reason"] == "last_page"
    assert finished_job.artifacts["archive_signature"]["floor_count"] == 3


def test_sync_thread_respects_archive_thread_max_pages_for_non_novel_threads(db, tmp_path, monkeypatch):
    settings = _make_settings(tmp_path)
    settings.archive_thread_max_pages = 2
    repo = JobsRepository(db)
    job = repo.create("sync_thread", tid=42, payload={"tid": 42, "forum_id": 30})
    job = repo.acquire(job.job_id, "worker-1", 300)

    page1 = _make_snapshot()
    page2 = ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42&page=2",
        page_type="thread_detail",
        raw_title=page1.raw_title,
        display_title=page1.display_title,
        title=page1.title,
        publisher=page1.publisher,
        publisher_uid=page1.publisher_uid,
        pub_time=page1.pub_time,
        permission=0,
        floors=[
            FloorSnapshot(
                pid=1002,
                tid=42,
                floor_no=1,
                publisher="u2",
                content="第二页正文",
                pub_time="2025-01-02 00:00",
                has_images=False,
                image_urls=[],
            )
        ],
        image_count=0,
    )
    snapshots_by_url = {snap.url: snap for snap in (page1, page2)}

    class _FakeClient:
        headers = {}
        cookie_jar = None
        use_system_proxy = False

        def fetch_thread(self, **kwargs):
            return SimpleNamespace(html="page-1", final_url=page1.url)

        def fetch_thread_pages(self, **kwargs):
            assert kwargs["max_pages"] == 2
            return (
                [
                    SimpleNamespace(html="page-1", final_url=page1.url),
                    SimpleNamespace(html="page-2", final_url=page2.url),
                ],
                8,
                "max_pages",
            )

    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.YamiboClient", lambda **kwargs: _FakeClient())
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.parse_thread_snapshot",
        lambda html, url=None, tid=None: snapshots_by_url[url],
    )
    monkeypatch.setattr(
        "yamibo_mcp.daemon.handlers.sync_thread.refine_title_parse_with_llm",
        lambda settings, raw_title, parsed: (parsed, None),
    )
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.write_staging_title_parse_log", lambda *args, **kwargs: None)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.write_staging_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr("yamibo_mcp.daemon.handlers.sync_thread.update_title_hints", lambda *args, **kwargs: None)
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
            missing_urls=[],
            missing_shared_urls=[],
        ),
    )

    handle_sync_thread(repo, job, "worker-1", 300, settings)

    finished_job = repo.get(job.job_id)

    assert finished_job.artifacts["pages_fetched"] == 2
    assert finished_job.artifacts["stopped_reason"] == "max_pages"
    assert finished_job.artifacts["archive_signature"]["floor_count"] == 2
