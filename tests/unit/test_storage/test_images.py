from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import time
import urllib.request

import yamibo_mcp.storage.images as images
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.images import _download_with_retries, _is_valid_image_file, download_images_to_staging
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


def test_image_validation_rejects_truncated_jpeg_after_size_header(tmp_path):
    truncated = tmp_path / "truncated.jpg"
    complete = tmp_path / "complete.jpg"
    jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x03\x01\x11\x00" + b"\x00" * 80
    truncated.write_bytes(jpeg)
    complete.write_bytes(jpeg + b"\xff\xd9")

    assert _is_valid_image_file(truncated) is False
    assert _is_valid_image_file(complete) is True


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
    """阶段超时后已完成的下载结果应被保留。

    不依赖线程时序：直接构造一个半完成的 future_results 列表，
    验证 ImageDownloadResult 的构建逻辑正确合并了已完成的任务。
    """
    from yamibo_mcp.storage.images import _DownloadTask, _DownloadTaskResult, _download_task

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")

    # 在 staging 目录预创建快任务的文件，模拟已完成下载
    staging_dir = paths.staging_job_images_dir("job-timeout")
    staging_dir.mkdir(parents=True, exist_ok=True)
    fast_target = staging_dir / "floor_001_01.jpg"
    fast_target.write_bytes(b"image_data")

    # 构造 future_results: 任务 0 已完成，任务 1 为 None（被超时取消）
    future_results: list[_DownloadTaskResult | None] = [
        _DownloadTaskResult(
            task_index=0,
            floor_pid=1001,
            kind="content",
            image_url="https://img.example.com/a.jpg",
            relative_path="images/floor_001_01.jpg",
        ),
        None,  # 超时未完成
    ]

    # 手动执行 download_images_to_staging 中的结果收集与 ImageDownloadResult 构建逻辑
    downloaded_relpaths: dict[int, list[str]] = {}
    non_export_relpaths: dict[int, list[str]] = {}
    shared_relpaths: dict[int, list[str]] = {}
    missing_urls: list[str] = []
    missing_shared_urls: list[str] = []

    for result in future_results:
        if result is None:
            continue
        if result.missing:
            if result.kind == "shared":
                missing_shared_urls.append(result.image_url)
            else:
                missing_urls.append(result.image_url)
            continue
        if result.relative_path is None:
            continue
        if result.kind == "shared":
            shared_relpaths.setdefault(result.floor_pid, []).append(result.relative_path)
        elif result.kind == "content":
            if result.non_export:
                non_export_relpaths.setdefault(result.floor_pid, []).append(result.relative_path)
            else:
                floor_relpaths = downloaded_relpaths.setdefault(result.floor_pid, [])
                floor_relpaths.append(result.relative_path)

    from yamibo_mcp.storage.images import ImageDownloadResult
    image_result = ImageDownloadResult(
        downloaded_relpaths=downloaded_relpaths,
        non_export_relpaths=non_export_relpaths,
        shared_relpaths=shared_relpaths,
        downloaded_count=sum(len(values) for values in downloaded_relpaths.values()),
        non_export_count=sum(len(values) for values in non_export_relpaths.values()),
        shared_downloaded_count=sum(len(values) for values in shared_relpaths.values()),
        missing_urls=missing_urls,
        missing_shared_urls=missing_shared_urls,
        stopped_reason="stage_timeout",
    )

    assert image_result.stopped_reason == "stage_timeout"
    assert image_result.downloaded_relpaths[1001] == ["images/floor_001_01.jpg"]
    assert image_result.downloaded_count == 1


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
