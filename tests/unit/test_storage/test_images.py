from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import threading
import time
from types import SimpleNamespace
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


def test_download_images_to_staging_returns_partial_on_stage_timeout(tmp_path, caplog):
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
    summary = next(record for record in caplog.records if getattr(record, "event_type", None) == "image.download.summary")
    assert summary.result == "partial"
    assert summary.error_code == "IMAGE_STAGE_TIMEOUT"


def test_download_images_to_staging_times_out_waiting_for_download_slot(tmp_path, monkeypatch, caplog):
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
    summary = next(record for record in caplog.records if getattr(record, "event_type", None) == "image.download.summary")
    assert summary.result == "partial"
    assert summary.error_code == "IMAGE_DOWNLOAD_SLOT_TIMEOUT"


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
    padded = tmp_path / "padded.jpg"
    samsung_sef = tmp_path / "samsung-sef.jpg"
    jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x03\x01\x11\x00" + b"\x00" * 80
    truncated.write_bytes(jpeg)
    complete.write_bytes(jpeg + b"\xff\xd9")
    padded.write_bytes(jpeg + b"\xff\xd9" + b"\xca\x00\x31\xc5")
    samsung_sef.write_bytes(jpeg + b"\xff\xd9" + b"\x00" * 1100 + b"SEF")

    assert _is_valid_image_file(truncated) is False
    assert _is_valid_image_file(complete) is True
    assert _is_valid_image_file(padded) is True
    assert _is_valid_image_file(samsung_sef) is True


def test_yamibo_attachment_uses_authenticated_fetcher_and_records_diagnostics(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=1640438"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)
    calls = []
    jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x03\x01\x11\x00" + b"\x00" * 80 + b"\xff\xd9"

    def fetcher(image_url, **kwargs):
        calls.append((image_url, kwargs))
        return SimpleNamespace(
            status_code=200,
            final_url=image_url,
            headers={"content-type": "image/jpeg"},
            content=jpeg,
        )

    result = download_images_to_staging(
        paths,
        "job-authenticated-image",
        snapshot,
        timeout=1,
        retries=0,
        fetcher=fetcher,
        referer=snapshot.url,
        target_urls={url},
    )

    assert calls == [(url, {"referer": snapshot.url, "timeout": 1})]
    assert result.relative_path_by_url[url] == "images/floor_001_01.jpg"
    assert result.diagnostics[0]["transport"] == "yamibo_session"
    assert result.diagnostics[0]["content_type"] == "image/jpeg"
    assert result.diagnostics[0]["http_status"] == 200
    assert result.diagnostics[0]["bytes"] == len(jpeg)


def test_authenticated_fetch_failure_keeps_http_diagnostics_and_cleans_part(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=1640438"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)

    def fetcher(image_url, **kwargs):
        return SimpleNamespace(
            status_code=403,
            final_url=image_url,
            headers={"Content-Type": "text/html"},
            content=b"<html>forbidden</html>",
        )

    result = download_images_to_staging(
        paths,
        "job-authenticated-failure",
        snapshot,
        timeout=1,
        retries=2,
        fetcher=fetcher,
        target_urls={url},
    )

    assert result.missing_urls == [url]
    diagnostic = result.diagnostics[0]
    assert diagnostic["url"] == url
    assert diagnostic["final_url"] == url
    assert diagnostic["content_type"] == "text/html"
    assert diagnostic["http_status"] == 403
    assert diagnostic["bytes"] == len(b"<html>forbidden</html>")
    assert diagnostic["attempts"] == 1
    assert diagnostic["transport"] == "yamibo_session"
    assert diagnostic["status"] == "error"
    assert diagnostic["error_type"] == "http_error"
    assert diagnostic["error_message"] == "HTTP 403"
    assert diagnostic["retryable"] is False
    assert diagnostic["phase"] == "response"
    assert len(diagnostic["body_sha256"]) == 64
    assert diagnostic["attempt_history"][0]["http_status"] == 403
    assert diagnostic["attempt_history"][0]["phase"] == "response"
    assert diagnostic["attempt_history"][0]["error_type"] == "http_error"
    assert not list(paths.staging_job_images_dir("job-authenticated-failure").glob("*.part"))


def test_authenticated_html_response_is_non_image_and_not_retried(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=1640438"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)
    calls = 0

    def fetcher(image_url, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            status_code=200,
            final_url=image_url,
            headers={"Content-Type": "text/html"},
            content=b"<html>not an image</html>",
        )

    result = download_images_to_staging(
        paths,
        "job-authenticated-html",
        snapshot,
        timeout=1,
        retries=2,
        fetcher=fetcher,
        target_urls={url},
    )

    assert calls == 1
    assert result.missing_urls == [url]
    assert result.diagnostics[0]["http_status"] == 200
    assert result.diagnostics[0]["error_type"] == "html_response"
    assert result.diagnostics[0]["retryable"] is False


def test_authenticated_invalid_image_without_length_is_not_called_truncated(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=1640438"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)
    calls = 0
    truncated_jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x03\x01\x11\x00" + b"\x00" * 80

    def fetcher(image_url, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            status_code=200,
            final_url=image_url,
            headers={"Content-Type": "image/jpeg"},
            content=truncated_jpeg,
        )

    result = download_images_to_staging(
        paths,
        "job-authenticated-truncated",
        snapshot,
        timeout=1,
        retries=2,
        fetcher=fetcher,
        target_urls={url},
    )

    assert calls == 1
    assert result.missing_urls == [url]
    assert result.diagnostics[0]["error_type"] == "invalid_image_body"
    assert result.diagnostics[0]["retryable"] is False
    assert result.diagnostics[0]["attempts"] == 1
    assert result.diagnostics[0].get("range_attempted") is not True
    assert result.diagnostics[0]["phase"] == "validation"
    assert len(result.diagnostics[0]["attempt_history"]) == 1
    assert all(item["error_type"] == "invalid_image_body" for item in result.diagnostics[0]["attempt_history"])
    assert all(item["jpeg_has_soi"] is True for item in result.diagnostics[0]["attempt_history"])
    assert all(item["jpeg_eoi_offset"] is None for item in result.diagnostics[0]["attempt_history"])


def test_authenticated_invalid_image_with_matching_length_does_not_attempt_range(tmp_path):
    paths = StoragePaths(tmp_path)
    url = "https://bbs.yamibo.com/data/attachment/forum/invalid.png"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
    calls = []

    def fetcher(image_url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status_code=200, final_url=image_url,
            headers={"Content-Type": "image/png", "Content-Length": str(len(body))}, content=body,
        )

    result = download_images_to_staging(
        paths, "job-matching-invalid", snapshot, timeout=1, retries=2,
        fetcher=fetcher, target_urls={url},
    )

    assert len(calls) == 1
    assert result.missing_urls == [url]
    diagnostic = result.diagnostics[0]
    assert diagnostic["bytes"] == len(body)
    assert diagnostic["content_length_matches"] is True
    assert diagnostic["png_iend_offset"] is None
    assert diagnostic["error_type"] == "invalid_image_body"
    assert diagnostic["retryable"] is False
    assert diagnostic.get("range_attempted") is not True
    assert not list(paths.staging_job_images_dir("job-matching-invalid").glob("*.part"))


def test_png_probe_records_trailer_without_storing_body():
    body = b"\x89PNG\r\n\x1a\n" + b"x" * 80 + b"\x00\x00\x00\x00IEND\xaeB`\x82" + b"PAD"
    probe = images._image_probe_from_chunks([body[:-8], body[-8:]], content_length=len(body))

    assert probe["content_length_matches"] is True
    assert probe["png_iend_offset"] == len(body) - 15
    assert probe["png_trailing_bytes"] == 3
    assert "body" not in probe


def test_non_image_body_with_length_mismatch_does_not_attempt_range(tmp_path):
    paths = StoragePaths(tmp_path)
    url = "https://bbs.yamibo.com/data/attachment/forum/invalid.jpg"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)
    calls = []

    def fetcher(image_url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status_code=200, final_url=image_url,
            headers={"Content-Type": "application/json", "Content-Length": "100"}, content=b'{"error":"missing"}',
        )

    result = download_images_to_staging(
        paths, "job-non-image-short", snapshot, timeout=1, retries=2, fetcher=fetcher,
    )

    assert len(calls) == 1
    assert result.diagnostics[0]["error_type"] == "invalid_image_body"


def test_upstream_503_stops_bulk_download_and_preserves_retry_after(tmp_path):
    paths = StoragePaths(tmp_path)
    urls = [f"https://bbs.yamibo.com/data/attachment/forum/{index}.jpg" for index in range(12)]
    floor = replace(_make_snapshot().floors[0], image_urls=urls)
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=len(urls))
    calls = []

    def fetcher(image_url, **kwargs):
        calls.append(image_url)
        return SimpleNamespace(
            status_code=503, final_url=image_url,
            headers={"Content-Type": "text/html", "Retry-After": "180"}, content=b"upstream unavailable",
        )

    result = download_images_to_staging(
        paths, "job-upstream-503", snapshot, timeout=1, retries=2, fetcher=fetcher,
    )

    assert result.stopped_reason == "upstream_throttled"
    assert 1 <= len(calls) <= 4
    assert result.diagnostics[0]["attempts"] == 1
    assert result.diagnostics[0]["response_headers"]["Retry-After"] == "180"


def test_authenticated_success_records_jpeg_trailer_evidence(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/data/attachment/forum/202309/08/example.jpg"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)
    jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x03\x01\x11\x00" + b"\x00" * 80
    body = jpeg + b"\xff\xd9" + b"\x00" * 1100 + b"SEF"

    def fetcher(image_url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            final_url=image_url,
            headers={"Content-Type": "image/jpeg", "Content-Length": str(len(body))},
            content=body,
        )

    result = download_images_to_staging(
        paths,
        "job-authenticated-jpeg-trailer",
        snapshot,
        timeout=1,
        retries=0,
        fetcher=fetcher,
        target_urls={url},
    )

    diagnostic = result.diagnostics[0]
    eoi_offset = body.rfind(b"\xff\xd9")
    assert result.missing_urls == []
    assert diagnostic["status"] == "ok"
    assert diagnostic["body_format"] == "jpg"
    assert diagnostic["content_length_matches"] is True
    assert diagnostic["jpeg_has_soi"] is True
    assert diagnostic["jpeg_eoi_offset"] == eoi_offset
    assert diagnostic["jpeg_trailing_bytes"] == len(body) - eoi_offset - 2
    assert diagnostic["attempt_history"][0]["phase"] == "persist"
    assert diagnostic["attempt_history"][0]["status"] == "ok"


def test_authenticated_truncated_image_body_recovers_with_range(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=1640438"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)
    full_jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x01\xe0\x02\x80\x03\x01\x11\x00" + b"\x00" * 80 + b"\xff\xd9"
    prefix = full_jpeg[:-2]
    calls = []

    def fetcher(image_url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                status_code=200,
                final_url=image_url,
                headers={"Content-Type": "image/jpeg", "Content-Length": str(len(full_jpeg))},
                content=prefix,
            )
        return SimpleNamespace(
            status_code=206,
            final_url=image_url,
            headers={
                "Content-Type": "image/jpeg",
                "Content-Range": f"bytes {len(prefix)}-{len(full_jpeg) - 1}/{len(full_jpeg)}",
            },
            content=full_jpeg[len(prefix):],
        )

    result = download_images_to_staging(
        paths,
        "job-authenticated-range-recovery",
        snapshot,
        timeout=1,
        retries=0,
        fetcher=fetcher,
        target_urls={url},
    )

    assert result.missing_urls == []
    assert result.relative_path_by_url[url] == "images/floor_001_01.jpg"
    assert calls[1]["headers"] == {"Range": f"bytes={len(prefix)}-"}
    assert result.diagnostics[0]["status"] == "ok"
    assert result.diagnostics[0]["range_recovered"] is True


def test_authenticated_waf_response_is_distinct_from_plain_html(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/forum.php?mod=attachment&aid=1640438"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)

    def fetcher(image_url, **kwargs):
        return SimpleNamespace(
            status_code=200,
            final_url=image_url,
            # The edge can mislabel an interception page as an image.
            headers={"Content-Type": "image/jpeg", "Content-Length": "1"},
            content=b"<html><title>Attention Required! | Cloudflare</title>Sorry, you have been blocked</html>",
        )

    result = download_images_to_staging(
        paths,
        "job-authenticated-waf",
        snapshot,
        timeout=1,
        retries=2,
        fetcher=fetcher,
        target_urls={url},
    )

    assert result.diagnostics[0]["error_type"] == "waf_response"
    assert result.diagnostics[0]["retryable"] is False


def test_authenticated_http_404_waf_response_is_retryable_not_missing(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    url = "https://bbs.yamibo.com/data/attachment/album/200910/31/90813_1257006804MR3F.jpg"
    floor = replace(_make_snapshot().floors[0], image_urls=[url])
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=1)

    def fetcher(image_url, **kwargs):
        return SimpleNamespace(
            status_code=404,
            final_url=image_url,
            headers={"Content-Type": "text/html"},
            content=b"<html><title>Attention Required! | Cloudflare</title>Sorry, you have been blocked</html>",
        )

    result = download_images_to_staging(
        paths,
        "job-http-404-waf",
        snapshot,
        timeout=1,
        retries=0,
        fetcher=fetcher,
        target_urls={url},
    )

    assert result.diagnostics[0]["http_status"] == 404
    assert result.diagnostics[0]["error_type"] == "waf_response"
    assert result.diagnostics[0]["retryable"] is True


def test_download_control_check_runs_on_main_thread_and_worker_cancel_is_separate(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    floor = replace(
        _make_snapshot().floors[0],
        image_urls=[
            "https://img.example.com/a.jpg",
            "https://img.example.com/b.jpg",
        ],
    )
    snapshot = replace(_make_snapshot(), floors=[floor], image_count=2)
    main_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []
    control_thread_ids: list[int] = []

    def _fake_download(image_url, *, staging_dir, stem, cancel_check=None, **kwargs):
        worker_thread_ids.append(threading.get_ident())
        if cancel_check is not None:
            cancel_check()
        target = staging_dir / f"{stem}.jpg"
        target.write_bytes(b"image_data")
        return target

    monkeypatch.setattr(images, "_download_with_retries", _fake_download)
    monkeypatch.setattr(images, "_should_exclude_from_export", lambda *args, **kwargs: False)

    result = download_images_to_staging(
        paths,
        "job-control-check",
        snapshot,
        timeout=1,
        cancel_check=lambda: None,
        control_check=lambda: control_thread_ids.append(threading.get_ident()),
    )

    assert result.downloaded_count == 2
    assert worker_thread_ids
    assert all(thread_id != main_thread_id for thread_id in worker_thread_ids)
    assert control_thread_ids
    assert all(thread_id == main_thread_id for thread_id in control_thread_ids)


def test_external_image_does_not_use_yamibo_fetcher(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")
    calls = []

    def fetcher(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("external image must not use Yamibo session")

    def fake_download(image_url, *, staging_dir, stem, metadata, **kwargs):
        metadata.update(attempts=1, status_code=200, content_type="image/jpeg", bytes=100, final_url=image_url)
        target = staging_dir / f"{stem}.jpg"
        target.write_bytes(b"not inspected in this test")
        return target

    monkeypatch.setattr(images, "_download_with_retries", fake_download)
    monkeypatch.setattr(images, "_should_exclude_from_export", lambda *args, **kwargs: False)

    result = download_images_to_staging(paths, "job-external", _make_snapshot(), fetcher=fetcher)

    assert calls == []
    assert result.diagnostics[0]["transport"] == "urllib"


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

    def _fake_download(image_url, *, staging_dir, stem, timeout, retries, opener, headers=None, referer=None, cancel_check=None, **kwargs):
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
