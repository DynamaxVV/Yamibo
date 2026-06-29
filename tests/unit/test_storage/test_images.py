from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import time
import urllib.request

import yamibo_mcp.storage.images as images
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.images import _download_with_retries, download_images_to_staging
from yamibo_mcp.storage.paths import StoragePaths


def _make_snapshot() -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="测试帖子",
        display_title="测试帖子",
        group_name=None,
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
        publisher="作者",
        content="正文",
        pub_time="2026-01-01 00:00",
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
        publisher="作者",
        publisher_uid="100",
        pub_time="2026-01-01 00:00",
        permission=0,
        floors=[floor],
        image_count=1,
    )


def test_download_images_to_staging_returns_partial_on_stage_timeout(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")

    result = download_images_to_staging(
        paths,
        "job-1",
        _make_snapshot(),
        timeout=1,
        retries=0,
        cookie_file=tmp_path / "cookie.txt",
        stage_deadline_seconds=1e-9,
    )

    assert result.stopped_reason == "stage_timeout"
    assert result.downloaded_count == 0
    assert result.missing_urls == []


def test_download_images_to_staging_times_out_waiting_for_download_slot(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")

    @contextmanager
    def _timeout_slot(*args, **kwargs):
        raise images.CookieDownloadSlotTimeoutError("timed out waiting for cookie download slot")
        yield  # pragma: no cover - unreachable

    monkeypatch.setattr("yamibo_mcp.storage.images.acquire_cookie_download_slot", _timeout_slot)

    result = download_images_to_staging(
        paths,
        "job-2",
        _make_snapshot(),
        timeout=1,
        retries=0,
        cookie_file=tmp_path / "cookie.txt",
    )

    assert result.stopped_reason == "download_slot_timeout"
    assert result.downloaded_count == 0
    assert result.missing_urls == []


def test_download_with_retries_stops_at_retry_limit(tmp_path, monkeypatch):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[int] = []

    def _raise_timeout(*args, **kwargs):
        attempts.append(1)
        raise TimeoutError("request timed out")

    monkeypatch.setattr("yamibo_mcp.storage.images._fetch_to_path", _raise_timeout)
    monkeypatch.setattr("yamibo_mcp.storage.images.time.sleep", lambda *args, **kwargs: None)

    try:
        _download_with_retries(
            "https://img.example.com/a.jpg",
            staging_dir=staging_dir,
            stem="floor_001_01",
            timeout=1,
            retries=1,
            opener=object(),
        )
    except TimeoutError:
        pass

    assert len(attempts) == 2


def test_download_images_to_staging_keeps_input_order_with_parallel_completion(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")

    floor = FloorSnapshot(
        pid=1001,
        tid=42,
        floor_no=1,
        publisher="作者",
        content="正文",
        pub_time="2026-01-01 00:00",
        has_images=True,
        image_urls=[
            "https://img.example.com/a.jpg",
            "https://img.example.com/b.jpg",
            "https://img.example.com/c.jpg",
        ],
    )
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=3)

    def _fake_download(image_url, *, staging_dir, stem, timeout, retries, opener, headers=None, referer=None, cancel_check=None):
        delay = {"floor_001_01": 0.03, "floor_001_02": 0.01, "floor_001_03": 0.02}[stem]
        time.sleep(delay)
        target = staging_dir / f"{stem}.jpg"
        target.write_bytes(stem.encode("utf-8"))
        return target

    monkeypatch.setattr("yamibo_mcp.storage.images._download_with_retries", _fake_download)
    monkeypatch.setattr("yamibo_mcp.storage.images._should_exclude_from_export", lambda *args, **kwargs: False)

    result = download_images_to_staging(
        paths,
        "job-order",
        snapshot,
        timeout=1,
        retries=0,
        cookie_file=tmp_path / "cookie.txt",
        use_system_proxy=False,
    )

    assert result.downloaded_relpaths[1001] == [
        "images/floor_001_01.jpg",
        "images/floor_001_02.jpg",
        "images/floor_001_03.jpg",
    ]
    assert result.stopped_reason is None


def test_download_images_to_staging_returns_partial_on_stage_timeout_with_completed_results(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")

    floor = FloorSnapshot(
        pid=1001,
        tid=42,
        floor_no=1,
        publisher="作者",
        content="正文",
        pub_time="2026-01-01 00:00",
        has_images=True,
        image_urls=[
            "https://img.example.com/a.jpg",
            "https://img.example.com/b.jpg",
        ],
    )
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=2)

    def _fake_download(image_url, *, staging_dir, stem, timeout, retries, opener, headers=None, referer=None, cancel_check=None):
        if stem.endswith("_01"):
            time.sleep(0.01)
        else:
            time.sleep(0.3)
        target = staging_dir / f"{stem}.jpg"
        target.write_bytes(stem.encode("utf-8"))
        return target

    monkeypatch.setattr("yamibo_mcp.storage.images._download_with_retries", _fake_download)
    monkeypatch.setattr("yamibo_mcp.storage.images._should_exclude_from_export", lambda *args, **kwargs: False)

    result = download_images_to_staging(
        paths,
        "job-timeout",
        snapshot,
        timeout=1,
        retries=0,
        cookie_file=tmp_path / "cookie.txt",
        stage_deadline_seconds=0.05,
    )

    assert result.stopped_reason == "stage_timeout"
    assert result.downloaded_relpaths[1001] == ["images/floor_001_01.jpg"]


def test_download_task_explicit_proxy_url_constructs_proxy_handler(monkeypatch, tmp_path):
    """When proxy_url is passed to _download_task, opener uses explicit ProxyHandler."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    build_opener_calls = []

    original_build_opener = urllib.request.build_opener

    def _fake_build_opener(*handlers):
        build_opener_calls.append(handlers)
        return original_build_opener(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", _fake_build_opener)

    task = images._DownloadTask(
        task_index=0,
        floor_pid=1,
        image_url="https://img.example.com/a.jpg",
        kind="content",
        stem="test",
    )

    # patch _fetch_to_path to avoid actual network
    monkeypatch.setattr(images, "_fetch_to_path", lambda **kwargs: staging_dir / "dummy.jpg")

    images._download_task(
        task,
        paths=StoragePaths(tmp_path / "data", export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports"),
        staging_dir=staging_dir,
        timeout=1.0,
        retries=0,
        cookie_jar=None,
        use_system_proxy=False,
        proxy_url="http://127.0.0.1:7890",
        headers=None,
        referer=None,
        cancel_check=None,
    )

    proxy_handlers = [h for h in build_opener_calls[0] if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_download_task_no_proxy_url_uses_system_proxy_logic(monkeypatch, tmp_path):
    """Without proxy_url, behavior is unchanged."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    build_opener_calls: list[tuple] = []

    def _fake_build_opener(*handlers):
        build_opener_calls.append(handlers)
        return object()

    monkeypatch.setattr(urllib.request, "build_opener", _fake_build_opener)
    monkeypatch.setattr(images, "_fetch_to_path", lambda **kwargs: staging_dir / "dummy.jpg")

    task = images._DownloadTask(
        task_index=0,
        floor_pid=1,
        image_url="https://img.example.com/a.jpg",
        kind="content",
        stem="test",
    )

    # use_system_proxy=False, no proxy_url
    images._download_task(
        task,
        paths=StoragePaths(tmp_path / "data", export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports"),
        staging_dir=staging_dir,
        timeout=1.0,
        retries=0,
        cookie_jar=None,
        use_system_proxy=False,
        headers=None,
        referer=None,
        cancel_check=None,
    )

    handlers = build_opener_calls[0]
    proxy_handlers = [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}

    build_opener_calls.clear()

    # use_system_proxy=True, no proxy_url
    images._download_task(
        task,
        paths=StoragePaths(tmp_path / "data", export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports"),
        staging_dir=staging_dir,
        timeout=1.0,
        retries=0,
        cookie_jar=None,
        use_system_proxy=True,
        headers=None,
        referer=None,
        cancel_check=None,
    )

    handlers2 = build_opener_calls[0]
    proxy_handlers2 = [h for h in handlers2 if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers2) == 0
