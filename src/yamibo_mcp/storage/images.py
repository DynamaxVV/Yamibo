from __future__ import annotations

import logging
import os
import random
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from mimetypes import guess_extension
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Mapping, NoReturn
from urllib.parse import urlparse

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.structured_logging import emit
from yamibo_mcp.yamibo.anti_bot import is_http_429_error, is_http_444_error, is_soft_block_page
from yamibo_mcp.yamibo.urls import is_yamibo_site_content_image_url, stable_attachment_id
from yamibo_mcp.yamibo.runtime_limits import CookieDownloadSlotTimeoutError, acquire_cookie_download_slot, throttle_cookie_request


LOG = logging.getLogger(__name__)


class _ImageHTTPError(Exception):
    """Raised internally when an image download returns an HTTP error."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.retryable = status_code in {408, 425, 429} or status_code >= 500


class _ImageContentError(ValueError):
    _MESSAGES = {
        "waf_response": "WAF interception page returned instead of image",
        "html_response": "HTML response returned instead of image",
        "truncated_image": "image response body is truncated or incomplete",
        "invalid_image_body": "invalid image response body",
    }

    def __init__(self, error_type: str, *, retryable: bool | None = None) -> None:
        super().__init__(self._MESSAGES.get(error_type, "invalid image response"))
        self.error_type = error_type
        # A truncated body is often caused by a transient upstream/proxy
        # transport problem and is safe to retry.  HTML/WAF and otherwise
        # invalid bodies are deterministic enough to avoid retry storms.
        self.retryable = error_type == "truncated_image" if retryable is None else retryable


@dataclass(frozen=True)
class _FetchedImageResponse:
    status_code: int
    final_url: str
    headers: Mapping[str, Any]
    body: bytes


@dataclass(frozen=True)
class ImageDownloadResult:
    downloaded_relpaths: dict[int, list[str]] = field(default_factory=dict)
    non_export_relpaths: dict[int, list[str]] = field(default_factory=dict)
    shared_relpaths: dict[int, list[str]] = field(default_factory=dict)
    skipped_relpaths: dict[int, list[str]] = field(default_factory=dict)
    downloaded_count: int = 0
    non_export_count: int = 0
    shared_downloaded_count: int = 0
    missing_urls: list[str] = field(default_factory=list)
    missing_shared_urls: list[str] = field(default_factory=list)
    stopped_reason: str | None = None
    # The list-shaped fields above are retained for archive artifacts and
    # backwards compatibility.  Callers that need to associate a URL with
    # its own original slot must use this direct map instead of zipping
    # successful downloads (failed downloads make that unsafe).
    relative_path_by_url: dict[str, str] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _DownloadTask:
    task_index: int
    floor_pid: int
    image_url: str
    kind: str
    stem: str | None = None
    target: Path | None = None


@dataclass(frozen=True)
class _DownloadTaskResult:
    task_index: int
    floor_pid: int
    kind: str
    image_url: str
    relative_path: str | None = None
    non_export: bool = False
    missing: bool = False
    diagnostic: dict[str, Any] = field(default_factory=dict)


ImageFetcher = Callable[..., Any]


def _download_task(
    task: _DownloadTask,
    *,
    paths: StoragePaths,
    staging_dir: Path,
    timeout: float,
    retries: int,
    cookie_jar: CookieJar | None,
    cookie_file: str | None = None,
    use_system_proxy: bool,
    proxy_url: str | None = None,
    headers: Mapping[str, str] | None,
    referer: str | None,
    cancel_check: Callable[[], None] | None,
    fetcher: ImageFetcher | None = None,
) -> _DownloadTaskResult:
    started_at = time.monotonic()
    effective_fetcher = fetcher if _use_authenticated_fetcher(task.image_url, fetcher) else None
    local_cookie_jar = CookieJar()
    if cookie_jar is not None:
        for cookie in cookie_jar:
            local_cookie_jar.set_cookie(cookie)
    handlers = [urllib.request.HTTPCookieProcessor(local_cookie_jar)]
    if proxy_url:
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    elif not use_system_proxy:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    metadata: dict[str, Any] = {}

    # Respect per-cookie rate limits for image fetches too
    throttle_cookie_request(cookie_file, request_interval=0.3, request_interval_jitter=0.2)
    if task.kind == "shared":
        assert task.target is not None
        try:
            if not task.target.exists() or not _is_valid_image_file(task.target):
                _download_to_explicit_target_with_retries(
                    task.image_url,
                    target=task.target,
                    timeout=timeout,
                    retries=retries,
                    opener=opener,
                    headers=headers,
                    referer=referer,
                    cancel_check=cancel_check,
                    fetcher=effective_fetcher,
                    metadata=metadata,
                )
        except Exception as exc:  # noqa: BLE001 - shared resource failure should not block archive
            return _DownloadTaskResult(task_index=task.task_index, floor_pid=task.floor_pid, kind=task.kind, image_url=task.image_url, missing=True,
                diagnostic=_diagnostic(task.image_url, exc=exc, attempts=int(metadata.get("attempts", 1)), duration_ms=_elapsed_ms(started_at), transport=_transport(effective_fetcher), metadata=metadata))
        return _DownloadTaskResult(
            task_index=task.task_index,
            floor_pid=task.floor_pid,
            kind=task.kind,
            image_url=task.image_url,
            relative_path=str(task.target.relative_to(paths.data_dir)),
            diagnostic=_diagnostic(task.image_url, status="ok", attempts=int(metadata.get("attempts", 1)), duration_ms=_elapsed_ms(started_at), transport=_transport(effective_fetcher), metadata=metadata),
        )

    assert task.stem is not None
    try:
        download_kwargs = dict(staging_dir=staging_dir, stem=task.stem, timeout=timeout, retries=retries,
                               opener=opener, headers=headers, referer=referer, cancel_check=cancel_check)
        if effective_fetcher is not None:
            download_kwargs["fetcher"] = effective_fetcher
        download_kwargs["metadata"] = metadata
        target = _download_with_retries(task.image_url, **download_kwargs)
    except Exception as exc:  # noqa: BLE001 - image failure should be tracked as missing
        return _DownloadTaskResult(task_index=task.task_index, floor_pid=task.floor_pid, kind=task.kind, image_url=task.image_url, missing=True,
            diagnostic=_diagnostic(task.image_url, exc=exc, attempts=int(metadata.get("attempts", 1)), duration_ms=_elapsed_ms(started_at), transport=_transport(effective_fetcher), metadata=metadata))
    relative = f"images/{target.name}"
    return _DownloadTaskResult(
        task_index=task.task_index,
        floor_pid=task.floor_pid,
        kind=task.kind,
        image_url=task.image_url,
        relative_path=relative,
        non_export=_should_exclude_from_export(target, image_url=task.image_url),
        diagnostic=_diagnostic(task.image_url, status="ok", attempts=int(metadata.get("attempts", 1)), duration_ms=_elapsed_ms(started_at), transport=_transport(effective_fetcher), metadata=metadata),
    )


def download_images_to_staging(
    paths: StoragePaths,
    job_id: str,
    snapshot: ThreadSnapshot,
    *,
    timeout: float = 15.0,
    retries: int = 0,
    headers: Mapping[str, str] | None = None,
    cookie_jar: CookieJar | None = None,
    cookie_file: str | Path | None = None,
    use_system_proxy: bool = False,
    proxy_url: str | None = None,
    referer: str | None = None,
    on_progress: Callable[[], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
    control_check: Callable[[], None] | None = None,
    stage_deadline_seconds: float | None = None,
    download_slot_wait_seconds: float | None = None,
    target_urls: set[str] | None = None,
    fetcher: ImageFetcher | None = None,
) -> ImageDownloadResult:
    """Download image targets without invoking database callbacks in workers.

    ``cancel_check`` is passed to download workers and therefore must be
    thread-safe.  ``control_check`` is called only by this function's caller
    thread while it is collecting work.  Job handlers use an in-memory event
    for the worker callback and keep database-backed cancellation/pause checks
    in ``control_check``.
    """
    started_at = time.monotonic()
    slot_wait_seconds = timeout if download_slot_wait_seconds is None else download_slot_wait_seconds
    try:
        with acquire_cookie_download_slot(cookie_file, timeout=slot_wait_seconds):
            staging_dir = paths.staging_job_images_dir(job_id)
            staging_dir.mkdir(parents=True, exist_ok=True)
            downloaded_relpaths: dict[int, list[str]] = {}
            non_export_relpaths: dict[int, list[str]] = {}
            shared_relpaths: dict[int, list[str]] = {}
            skipped_relpaths: dict[int, list[str]] = {}
            missing_urls: list[str] = []
            missing_shared_urls: list[str] = []
            diagnostics: list[dict[str, Any]] = []
            stopped_reason: str | None = None
            relative_path_by_url: dict[str, str] = {}
            tasks: list[_DownloadTask] = []
            shared_targets: dict[Path, str] = {}

            def _stage_timed_out() -> bool:
                return stage_deadline_seconds is not None and stage_deadline_seconds > 0 and (time.monotonic() - started_at) >= stage_deadline_seconds

            def _stop_if_timed_out() -> bool:
                nonlocal stopped_reason
                if stopped_reason is not None:
                    return True
                if _stage_timed_out():
                    stopped_reason = "stage_timeout"
                    LOG.info(
                        "Image download stage timed out job_id=%s elapsed=%.1fs deadline=%.1fs",
                        job_id,
                        time.monotonic() - started_at,
                        stage_deadline_seconds,
                    )
                    return True
                return False

            def _main_control_check() -> None:
                # Keep the legacy callback useful for callers that do not need
                # a separate main-thread control callback.  Production job
                # handlers pass both callbacks explicitly.
                check = control_check or cancel_check
                if check is not None:
                    check()

            for floor in snapshot.floors:
                _main_control_check()
                if _stop_if_timed_out():
                    break
                for index, image_url in enumerate(floor.image_urls, start=1):
                    # Keep the original position in ``floor.image_urls`` for
                    # the stem.  Selected/retry jobs filter after this point
                    # so floor_001_23 never becomes floor_001_01.
                    if target_urls is not None and image_url not in target_urls:
                        continue
                    if _is_embedded_image_url(image_url):
                        skipped_relpaths.setdefault(floor.pid, []).append(image_url)
                        if on_progress is not None:
                            on_progress()
                        continue
                    if _is_shared_forum_asset(image_url):
                        shared_target = _shared_target_path(paths, image_url)
                        task_key = shared_target.resolve(strict=False)
                        if task_key in shared_targets:
                            skipped_relpaths.setdefault(floor.pid, []).append(image_url)
                            if on_progress is not None:
                                on_progress()
                            continue
                        shared_targets[task_key] = image_url
                        tasks.append(
                            _DownloadTask(
                                task_index=len(tasks),
                                floor_pid=floor.pid,
                                image_url=image_url,
                                kind="shared",
                                target=shared_target,
                            )
                        )
                        continue
                    if target_urls is None and not _is_exportable_content_image(snapshot, floor):
                        skipped_relpaths.setdefault(floor.pid, []).append(image_url)
                        if on_progress is not None:
                            on_progress()
                        continue
                    tasks.append(
                        _DownloadTask(
                            task_index=len(tasks),
                            floor_pid=floor.pid,
                            image_url=image_url,
                            kind="content",
                            stem=f"floor_{floor.floor_no:03d}_{index:02d}",
                        )
                    )
                if not floor.image_urls:
                    if on_progress is not None:
                        on_progress()

            if tasks:
                max_workers = min(4, len(tasks))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    task_kwargs = {
                        "paths": paths,
                        "staging_dir": staging_dir,
                        "timeout": timeout,
                        "retries": retries,
                        "cookie_jar": cookie_jar,
                        "cookie_file": str(cookie_file) if cookie_file else None,
                        "use_system_proxy": use_system_proxy,
                        "proxy_url": proxy_url,
                        "headers": headers,
                        "referer": referer,
                        "cancel_check": cancel_check,
                    }
                    if fetcher is not None:
                        task_kwargs["fetcher"] = fetcher
                    futures = {
                        executor.submit(
                            _download_task,
                            task,
                            **task_kwargs,
                        ): task.task_index
                        for task in tasks
                    }
                    future_results: list[_DownloadTaskResult | None] = [None] * len(tasks)
                    while futures:
                        _main_control_check()
                        if _stop_if_timed_out():
                            stopped_reason = stopped_reason or "stage_timeout"
                            for future in futures:
                                future.cancel()
                            break
                        done, _ = wait(tuple(futures), timeout=0.2, return_when=FIRST_COMPLETED)
                        if not done:
                            continue
                        for future in done:
                            task_index = futures.pop(future)
                            result = future.result()
                            future_results[task_index] = result
                            if on_progress is not None:
                                on_progress()

                for result in future_results:
                    if result is None:
                        continue
                    if result.diagnostic:
                        diagnostics.append(result.diagnostic)
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
                        relative_path_by_url[result.image_url] = result.relative_path
                    elif result.kind == "content":
                        relative_path_by_url[result.image_url] = result.relative_path
                        if result.non_export:
                            non_export_relpaths.setdefault(result.floor_pid, []).append(result.relative_path)
                        else:
                            floor_relpaths = downloaded_relpaths.setdefault(result.floor_pid, [])
                            floor_relpaths.append(result.relative_path)

                if stopped_reason is None and _stage_timed_out():
                    stopped_reason = "stage_timeout"

            result = ImageDownloadResult(
                downloaded_relpaths=downloaded_relpaths,
                non_export_relpaths=non_export_relpaths,
                shared_relpaths=shared_relpaths,
                skipped_relpaths=skipped_relpaths,
                downloaded_count=sum(len(values) for values in downloaded_relpaths.values()),
                non_export_count=sum(len(values) for values in non_export_relpaths.values()),
                shared_downloaded_count=sum(len(values) for values in shared_relpaths.values()),
                missing_urls=missing_urls,
                missing_shared_urls=missing_shared_urls,
                stopped_reason=stopped_reason,
                relative_path_by_url=relative_path_by_url,
                diagnostics=diagnostics,
            )
            _emit_download_diagnostics(job_id=job_id, tid=snapshot.tid, result=result)
            return result
    except CookieDownloadSlotTimeoutError:
        LOG.warning(
            "Image download slot timed out job_id=%s cookie_file=%s wait_seconds=%s",
            job_id,
            cookie_file,
            slot_wait_seconds,
        )
        result = ImageDownloadResult(stopped_reason="download_slot_timeout")
        _emit_download_diagnostics(job_id=job_id, tid=snapshot.tid, result=result)
        return result


def materialize_staged_images(paths: StoragePaths, job_id: str, tid: int) -> None:
    staging_dir = paths.staging_job_images_dir(job_id)
    if not staging_dir.exists():
        return
    thread_images_dir = paths.thread_images_dir(tid)
    thread_images_dir.mkdir(parents=True, exist_ok=True)
    # 先在 staging 下载，再复制到正式归档目录，方便后续继续演进成更严格的 finalize 检查。
    for path in staging_dir.iterdir():
        # A failed/cancelled worker must never promote a partial marker.  The
        # downloader normally removes it, but this guard also protects a
        # resumed job from stale staging residue.
        if path.is_file() and not path.name.endswith(".part"):
            shutil.copy2(path, thread_images_dir / path.name)


def _guess_suffix(image_url: str) -> str:
    parsed = urllib.parse.urlparse(image_url)
    suffix = Path(parsed.path).suffix
    if suffix and len(suffix) <= 8 and suffix.lower() != ".php":
        return suffix
    return ".bin"


def _download_with_retries(
    image_url: str,
    *,
    staging_dir: Path,
    stem: str,
    timeout: float,
    retries: int,
    opener,
    headers: Mapping[str, str] | None = None,
    referer: str | None = None,
    cancel_check: Callable[[], None] | None = None,
    fetcher: ImageFetcher | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if metadata is not None:
                metadata["attempts"] = attempt + 1
            return _fetch_to_path(
                image_url,
                staging_dir=staging_dir,
                stem=stem,
                timeout=timeout,
                opener=opener,
                headers=headers,
                referer=referer,
                cancel_check=cancel_check,
                fetcher=fetcher,
                metadata=metadata,
            )
        except _ImageHTTPError as exc:
            last_error = exc
            if exc.status_code in {429, 444} or not exc.retryable or attempt + 1 >= attempts:
                raise
            time.sleep(min(0.5 * (attempt + 1), 2.0))
        except Exception as exc:  # noqa: BLE001 - 上层只关心最终是否成功
            last_error = exc
            if attempt + 1 >= attempts or not _should_retry_exception(exc, metadata=metadata):
                break
            time.sleep(min(0.5 * (attempt + 1), 2.0))
    assert last_error is not None
    raise last_error


def _download_to_explicit_target_with_retries(
    image_url: str,
    *,
    target: Path,
    timeout: float,
    retries: int,
    opener,
    headers: Mapping[str, str] | None = None,
    referer: str | None = None,
    cancel_check: Callable[[], None] | None = None,
    fetcher: ImageFetcher | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if metadata is not None:
                metadata["attempts"] = attempt + 1
            _fetch_to_explicit_target(
                image_url,
                target=target,
                timeout=timeout,
                opener=opener,
                headers=headers,
                referer=referer,
                cancel_check=cancel_check,
                fetcher=fetcher,
                metadata=metadata,
            )
            return
        except _ImageHTTPError as exc:
            last_error = exc
            if exc.status_code in {429, 444} or not exc.retryable or attempt + 1 >= attempts:
                raise
            time.sleep(min(0.5 * (attempt + 1), 2.0))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 >= attempts or not _should_retry_exception(exc, metadata=metadata):
                break
            time.sleep(min(0.5 * (attempt + 1), 2.0))
    assert last_error is not None
    raise last_error


def _classify_invalid_image_body(body: bytes, *, content_type: str | None = None) -> str:
    """Classify an HTTP body that failed image validation.

    A response can have HTTP 200 and an image content type while still being
    truncated.  Keep that case distinct from an HTML/WAF interception page so
    callers can choose an appropriate retry policy.
    """
    text = body.decode("utf-8", errors="ignore")
    if is_soft_block_page(text):
        return "waf_response"

    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    stripped = body.lstrip().lower()
    if normalized_type in {"text/html", "application/xhtml+xml"} or stripped.startswith(
        (b"<!doctype html", b"<html", b"<head", b"<body")
    ):
        return "html_response"

    if normalized_type.startswith("image/") or _suffix_from_bytes(body[:64]) is not None:
        return "truncated_image"
    return "invalid_image_body"


def _content_length_mismatch(body: bytes, content_length: Any) -> bool:
    if content_length in {None, ""}:
        return False
    try:
        expected = int(str(content_length).strip())
    except (TypeError, ValueError):
        return False
    return expected >= 0 and expected != len(body)


def _raise_invalid_image_body(
    body: bytes,
    *,
    content_type: str | None = None,
    content_length: Any = None,
) -> NoReturn:
    error_type = _classify_invalid_image_body(body, content_type=content_type)
    # A WAF/HTML page can also have a misleading or incomplete length header.
    # Content classification must win over transport-length heuristics.
    if error_type in {"waf_response", "html_response"}:
        raise _ImageContentError(error_type)
    if _content_length_mismatch(body, content_length):
        raise _ImageContentError("truncated_image")
    raise _ImageContentError(error_type)


def _raise_image_http_error(status: int, *, body: bytes = b"") -> NoReturn:
    """Keep an HTML/WAF body from being mistaken for a missing image."""
    if body and is_soft_block_page(body.decode("utf-8", errors="ignore")):
        raise _ImageContentError("waf_response", retryable=True)
    raise _ImageHTTPError(status)


def _call_image_fetcher(
    fetcher: ImageFetcher,
    image_url: str,
    *,
    referer: str | None,
    timeout: float,
    request_headers: Mapping[str, str] | None = None,
) -> Any:
    """Call an authenticated image fetcher, preserving compatibility with old adapters.

    The normal request deliberately keeps the existing fetcher contract.  A
    Range header is only added for the bounded continuation request below;
    older test/custom fetchers that do not accept ``headers`` can still serve
    the initial request and simply skip continuation recovery.
    """
    kwargs: dict[str, Any] = {"referer": referer, "timeout": timeout}
    if request_headers:
        kwargs["headers"] = dict(request_headers)
        try:
            return fetcher(image_url, **kwargs)
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc) or "headers" not in str(exc):
                raise
            return fetcher(image_url, referer=referer, timeout=timeout)
    return fetcher(image_url, **kwargs)


def _response_from_fetcher(response: Any, *, image_url: str) -> _FetchedImageResponse:
    return _FetchedImageResponse(
        status_code=int(getattr(response, "status_code", 200)),
        final_url=str(getattr(response, "final_url", image_url)),
        headers=getattr(response, "headers", {}) or {},
        body=bytes(getattr(response, "content", b"")),
    )


def _record_response_metadata(
    metadata: dict[str, Any] | None,
    response: _FetchedImageResponse,
) -> None:
    if metadata is None:
        return
    metadata.update(
        status_code=response.status_code,
        final_url=response.final_url,
        content_type=_header_value(response.headers, "Content-Type"),
        content_length=_header_value(response.headers, "Content-Length"),
        content_range=_header_value(response.headers, "Content-Range"),
        bytes=len(response.body),
    )


def _parse_content_range(value: str | None) -> tuple[int, int, int | None] | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*", value, flags=re.IGNORECASE)
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2))
    total = None if match.group(3) == "*" else int(match.group(3))
    if end < start or (total is not None and end >= total):
        return None
    return start, end, total


def _resume_truncated_authenticated_image(
    image_url: str,
    *,
    body: bytes,
    fetcher: ImageFetcher | None,
    referer: str | None,
    timeout: float,
    metadata: dict[str, Any] | None,
) -> _FetchedImageResponse | None:
    """Try one HTTP Range continuation after a 200 image body is incomplete.

    Some attachment edges close a response after a valid image prefix while
    still returning HTTP 200.  A fresh full GET repeats the same failure, so a
    single ``bytes=<received>-`` continuation is a safer recovery path.  If
    the edge ignores Range or does not advertise a valid Content-Range, the
    caller keeps the original failure classification.
    """
    if fetcher is None or not body:
        return None
    # Keep continuation bounded across the outer retry loop.  If the edge
    # ignores Range, repeating that extra request for every full retry only
    # adds load without improving recovery odds.
    if metadata is not None and metadata.get("range_attempted"):
        return None
    if metadata is not None:
        metadata["range_attempted"] = True
    response = _response_from_fetcher(
        _call_image_fetcher(
            fetcher,
            image_url,
            referer=referer,
            timeout=timeout,
            request_headers={"Range": f"bytes={len(body)}-"},
        ),
        image_url=image_url,
    )
    if response.status_code >= 400:
        # A second response may reveal that the remote edge is now blocking
        # the session.  Preserve that stronger classification; ordinary
        # unsupported-range errors leave the original truncation evidence.
        if is_soft_block_page(response.body.decode("utf-8", errors="ignore")):
            _raise_image_http_error(response.status_code, body=response.body)
        return None
    if response.status_code == 206:
        if is_soft_block_page(response.body.decode("utf-8", errors="ignore")):
            raise _ImageContentError("waf_response")
        content_range = _parse_content_range(_header_value(response.headers, "Content-Range"))
        if (
            content_range is None
            or content_range[0] != len(body)
            or content_range[1] - content_range[0] + 1 != len(response.body)
        ):
            return None
        combined = _FetchedImageResponse(
            status_code=200,
            final_url=response.final_url,
            headers=response.headers,
            body=body + response.body,
        )
        _record_response_metadata(metadata, combined)
        if metadata is not None:
            metadata["range_recovered"] = True
        return combined

    if response.status_code == 200:
        # A server that ignores Range may still return the complete object.
        # The caller validates it before accepting the recovery.
        response_error_type = _classify_invalid_image_body(
            response.body,
            content_type=_header_value(response.headers, "Content-Type"),
        )
        if response_error_type == "waf_response":
            raise _ImageContentError("waf_response")
        if response_error_type == "html_response":
            return None
        _record_response_metadata(metadata, response)
        return response
    return None


def _write_and_validate_authenticated_image(
    image_url: str,
    *,
    part_target: Path,
    response: _FetchedImageResponse,
    fetcher: ImageFetcher | None,
    referer: str | None,
    timeout: float,
    metadata: dict[str, Any] | None,
) -> _FetchedImageResponse:
    """Write and validate a response, with one bounded Range recovery."""
    part_target.write_bytes(response.body)
    content_type = _header_value(response.headers, "Content-Type")
    content_length = _header_value(response.headers, "Content-Length")
    length_mismatch = _content_length_mismatch(response.body, content_length)
    try:
        if not length_mismatch:
            _require_valid_image_file(part_target)
            return response
        # A length mismatch is only a transport signal for an image-like
        # response.  WAF/HTML classification must still take precedence.
        if _classify_invalid_image_body(response.body, content_type=content_type) != "truncated_image":
            _raise_invalid_image_body(
                response.body,
                content_type=content_type,
                content_length=content_length,
            )
        raise _ImageContentError("truncated_image")
    except ValueError:
        error_type = _classify_invalid_image_body(
            response.body,
            content_type=content_type,
        )
        if error_type != "truncated_image":
            _raise_invalid_image_body(
                response.body,
                content_type=content_type,
                content_length=content_length,
            )
        recovered = _resume_truncated_authenticated_image(
            image_url,
            body=response.body,
            fetcher=fetcher,
            referer=referer,
            timeout=timeout,
            metadata=metadata,
        )
        if recovered is None:
            _raise_invalid_image_body(
                response.body,
                content_type=content_type,
                content_length=content_length,
            )
        recovered_content_range = _header_value(recovered.headers, "Content-Range")
        if (
            recovered.status_code == 200
            and not recovered_content_range
            and _content_length_mismatch(
                recovered.body,
                _header_value(recovered.headers, "Content-Length"),
            )
        ):
            _raise_invalid_image_body(
                recovered.body,
                content_type=_header_value(recovered.headers, "Content-Type"),
                content_length=_header_value(recovered.headers, "Content-Length"),
            )
        part_target.write_bytes(recovered.body)
        try:
            _require_valid_image_file(part_target)
        except ValueError:
            _raise_invalid_image_body(
                recovered.body,
                content_type=_header_value(recovered.headers, "Content-Type"),
                # Content-Length on a 206 response describes only the
                # continuation, not the combined object.
                content_length=None,
            )
        return recovered


def _fetch_to_path(
    image_url: str,
    *,
    staging_dir: Path,
    stem: str,
    timeout: float,
    opener,
    headers: Mapping[str, str] | None = None,
    referer: str | None = None,
    cancel_check: Callable[[], None] | None = None,
    fetcher: ImageFetcher | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(image_url)
    # 本地样例页里的 file:// 图片直接复制；远端 http(s) 图片通过 urllib 下载。
    target: Path | None = None
    part_target: Path | None = None
    try:
        if parsed.scheme == "file":
            if cancel_check is not None:
                cancel_check()
            source = Path(urllib.request.url2pathname(parsed.path))
            target = staging_dir / f"{stem}{_guess_suffix(image_url)}"
            part_target = target.with_name(target.name + ".part")
            shutil.copy2(source, part_target)
            _require_valid_image_file(part_target)
            os.replace(part_target, target)
            if cancel_check is not None:
                cancel_check()
            return target
        if parsed.scheme in {"http", "https"}:
            time.sleep(random.uniform(0.1, 0.5))
            request_headers = dict(headers or {})
            if referer:
                request_headers.setdefault("Referer", referer)
            if fetcher is not None:
                response = _response_from_fetcher(
                    _call_image_fetcher(
                        fetcher,
                        image_url,
                        referer=referer,
                        timeout=timeout,
                    ),
                    image_url=image_url,
                )
                _record_response_metadata(metadata, response)
                if response.status_code >= 400:
                    _raise_image_http_error(response.status_code, body=response.body)
                suffix = _suffix_from_response_headers(
                    response.headers,
                    image_url=image_url,
                    final_url=response.final_url,
                    first_bytes=response.body[:64],
                )
                target = staging_dir / f"{stem}{suffix}"
                part_target = target.with_name(target.name + ".part")
                response = _write_and_validate_authenticated_image(
                    image_url,
                    part_target=part_target,
                    response=response,
                    fetcher=fetcher,
                    referer=referer,
                    timeout=timeout,
                    metadata=metadata,
                )
                os.replace(part_target, target)
                return target
            request = urllib.request.Request(image_url, headers=request_headers)
            with opener.open(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                content_type = _header_value(response.headers, "Content-Type")
                content_length = _header_value(response.headers, "Content-Length")
                if metadata is not None:
                    metadata.update(
                        status_code=int(status),
                        final_url=getattr(response, "geturl", lambda: image_url)(),
                        content_type=content_type,
                        content_length=content_length,
                        content_range=_header_value(response.headers, "Content-Range"),
                        bytes=0,
                    )
                if status >= 400:
                    LOG.warning("Image download returned HTTP %d for %s", status, image_url)
                    _raise_image_http_error(status)
                first_bytes = response.read(64)
                if cancel_check is not None:
                    cancel_check()
                suffix = _suffix_from_response(response, image_url=image_url, first_bytes=first_bytes)
                target = staging_dir / f"{stem}{suffix}"
                part_target = target.with_name(target.name + ".part")
                with part_target.open("wb") as fp:
                    if first_bytes:
                        fp.write(first_bytes)
                    while True:
                        if cancel_check is not None:
                            cancel_check()
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        fp.write(chunk)
                if metadata is not None:
                    metadata["bytes"] = part_target.stat().st_size
                try:
                    _require_valid_image_file(part_target)
                except ValueError:
                    _raise_invalid_image_body(
                        part_target.read_bytes(),
                        content_type=content_type,
                        content_length=content_length,
                    )
                os.replace(part_target, target)
            return target
        source = Path(image_url)
        if source.exists():
            if cancel_check is not None:
                cancel_check()
            target = staging_dir / f"{stem}{source.suffix or '.bin'}"
            part_target = target.with_name(target.name + ".part")
            shutil.copy2(source, part_target)
            _require_valid_image_file(part_target)
            os.replace(part_target, target)
            if cancel_check is not None:
                cancel_check()
            return target
        raise FileNotFoundError(f"unsupported image source: {image_url}")
    except Exception:
        if part_target is not None:
            try:
                part_target.unlink()
            except FileNotFoundError:
                pass
        raise


def _fetch_to_explicit_target(
    image_url: str,
    *,
    target: Path,
    timeout: float,
    opener,
    headers: Mapping[str, str] | None = None,
    referer: str | None = None,
    cancel_check: Callable[[], None] | None = None,
    fetcher: ImageFetcher | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    part_target = target.with_name(target.name + ".part")
    try:
        parsed = urllib.parse.urlparse(image_url)
        if parsed.scheme == "file":
            if cancel_check is not None:
                cancel_check()
            source = Path(urllib.request.url2pathname(parsed.path))
            shutil.copy2(source, part_target)
        elif parsed.scheme in {"http", "https"}:
            time.sleep(random.uniform(0.1, 0.5))
            request_headers = dict(headers or {})
            if referer:
                request_headers.setdefault("Referer", referer)
            if fetcher is not None:
                response = _response_from_fetcher(
                    _call_image_fetcher(
                        fetcher,
                        image_url,
                        referer=referer,
                        timeout=timeout,
                    ),
                    image_url=image_url,
                )
                _record_response_metadata(metadata, response)
                if response.status_code >= 400:
                    _raise_image_http_error(response.status_code, body=response.body)
                _write_and_validate_authenticated_image(
                    image_url,
                    part_target=part_target,
                    response=response,
                    fetcher=fetcher,
                    referer=referer,
                    timeout=timeout,
                    metadata=metadata,
                )
            else:
                request = urllib.request.Request(image_url, headers=request_headers)
                with opener.open(request, timeout=timeout) as response, part_target.open("wb") as fp:
                    status = getattr(response, "status", 200)
                    content_type = _header_value(response.headers, "Content-Type")
                    content_length = _header_value(response.headers, "Content-Length")
                    if metadata is not None:
                        metadata.update(
                            status_code=int(status),
                            final_url=getattr(response, "geturl", lambda: image_url)(),
                            content_type=content_type,
                            content_length=content_length,
                            content_range=_header_value(response.headers, "Content-Range"),
                        )
                    if status >= 400:
                        LOG.warning("Image download returned HTTP %d for %s", status, image_url)
                        _raise_image_http_error(status)
                    while True:
                        if cancel_check is not None:
                            cancel_check()
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        fp.write(chunk)
        else:
            source = Path(image_url)
            if not source.exists():
                raise FileNotFoundError(f"unsupported image source: {image_url}")
            if cancel_check is not None:
                cancel_check()
            shutil.copy2(source, part_target)
        try:
            _require_valid_image_file(part_target)
        except ValueError:
            _raise_invalid_image_body(
                part_target.read_bytes(),
                content_type=(metadata or {}).get("content_type"),
                content_length=(metadata or {}).get("content_length"),
            )
        if metadata is not None:
            metadata["bytes"] = part_target.stat().st_size
        os.replace(part_target, target)
        if cancel_check is not None:
            cancel_check()
    except Exception:
        try:
            part_target.unlink()
        except FileNotFoundError:
            pass
        raise


def _suffix_from_response(response, *, image_url: str, first_bytes: bytes = b"") -> str:
    return _suffix_from_response_headers(response.headers, image_url=image_url, final_url=getattr(response, "geturl", lambda: image_url)(), first_bytes=first_bytes)


def _suffix_from_response_headers(headers, *, image_url: str, final_url: str, first_bytes: bytes = b"") -> str:
    content_type = (_header_value(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        mapped = guess_extension(content_type)
        if mapped == ".jpe":
            return ".jpg"
        if mapped:
            return mapped
    sniffed = _suffix_from_bytes(first_bytes)
    if sniffed is not None:
        return sniffed
    suffix = _guess_suffix(final_url)
    if suffix != ".bin":
        return suffix
    return _guess_suffix(image_url)


def _transport(fetcher: ImageFetcher | None) -> str:
    return "yamibo_session" if fetcher is not None else "urllib"


def _use_authenticated_fetcher(url: str, fetcher: ImageFetcher | None) -> bool:
    # All first-party post-content images should share the authenticated
    # Yamibo session. Legacy /data/attachment/... URLs can otherwise receive
    # misleading 404 responses when fetched through plain urllib without the
    # browser/session fingerprint used for thread pages.
    return fetcher is not None and is_yamibo_site_content_image_url(url)


def _header_value(headers: Mapping[str, Any], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == wanted:
            return str(value)
    return None


def _elapsed_ms(started_at: float) -> float:
    return round((time.monotonic() - started_at) * 1000.0, 1)


def _diagnostic(
    url: str,
    *,
    status: str = "error",
    exc: Exception | None = None,
    attempts: int = 1,
    duration_ms: float = 0.0,
    transport: str = "urllib",
    final_url: str | None = None,
    bytes: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    info = dict(metadata or {})
    details = getattr(exc, "details", {}) if exc is not None else {}
    if not isinstance(details, Mapping):
        details = {}
    http_status = info.get("status_code")
    if http_status is None and exc is not None:
        http_status = getattr(exc, "status_code", None) or getattr(exc, "code", None) or details.get("status_code")
    error_type = _error_type(exc, http_status=http_status)
    retryable = _is_retryable_image_error(exc, http_status=http_status, details=details)
    result = {
        "url": url,
        "final_url": info.get("final_url") or final_url or url,
        "content_type": info.get("content_type"),
        "http_status": http_status,
        "bytes": info.get("bytes", bytes),
        "attempts": attempts,
        "duration_ms": duration_ms,
        "transport": transport,
        "status": status,
        "error_type": error_type,
        "error_message": None if exc is None else _sanitize_error_message(str(exc)),
        "retryable": retryable,
    }
    for key in ("content_length", "content_range", "range_attempted", "range_recovered"):
        if info.get(key) is not None:
            result[key] = info[key]
    return result


def _error_type(exc: Exception | None, *, http_status: Any = None) -> str | None:
    if exc is None:
        return None
    explicit = getattr(exc, "error_type", None)
    if explicit:
        return str(explicit)
    if isinstance(exc, urllib.error.HTTPError) or (http_status is not None and int(http_status) >= 400):
        return "http_error"
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in message or "timeout" in name:
        return "timeout"
    if isinstance(exc, ValueError) and "image" in message:
        return "invalid_image_body"
    if "waf" in name or "challenge" in message or "soft block" in message:
        return "waf_response"
    if isinstance(exc, FileNotFoundError):
        return "not_found"
    return "network_error"


def _is_retryable_image_error(exc: Exception | None, *, http_status: Any, details: Mapping[str, Any]) -> bool | None:
    if exc is None:
        return None
    explicit = getattr(exc, "retryable", None)
    if explicit is not None:
        return bool(explicit)
    if "retryable" in details:
        return bool(details["retryable"])
    if http_status is not None and int(http_status) >= 400:
        status = int(http_status)
        return status in {408, 425, 429} or status >= 500
    if isinstance(exc, FileNotFoundError):
        return False
    return _error_type(exc, http_status=http_status) in {
        "timeout",
        "network_error",
        "waf_response",
        "truncated_image",
    }


def _should_retry_exception(exc: Exception, *, metadata: Mapping[str, Any] | None) -> bool:
    info = dict(metadata or {})
    details = getattr(exc, "details", {})
    if not isinstance(details, Mapping):
        details = {}
    http_status = info.get("status_code")
    if http_status is None:
        http_status = getattr(exc, "status_code", None) or getattr(exc, "code", None) or details.get("status_code")
    return bool(_is_retryable_image_error(exc, http_status=http_status, details=details))


def _sanitize_error_message(message: str) -> str:
    def _strip_query(match: re.Match[str]) -> str:
        parsed = urllib.parse.urlsplit(match.group(0))
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    return re.sub(r"https?://[^\s]+", _strip_query, message)[:1000]


def _safe_diagnostic_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in item.items() if key not in {"url", "final_url"}}
    for key in ("url", "final_url"):
        value = item.get(key)
        if not value:
            continue
        parsed = urllib.parse.urlsplit(str(value))
        payload[f"{key}_host"] = parsed.netloc
        payload[f"{key}_path"] = parsed.path
        attachment_id = stable_attachment_id(str(value))
        if attachment_id is not None:
            payload["remote_identity"] = attachment_id
    return payload


def _emit_download_diagnostics(*, job_id: str, tid: int, result: ImageDownloadResult) -> None:
    failures = [item for item in result.diagnostics if item.get("status") != "ok"]
    stopped = result.stopped_reason is not None
    for item in failures:
        payload = _safe_diagnostic_payload(item)
        error_type = str(item.get("error_type") or "download_failed")
        emit(
            LOG,
            logging.WARNING,
            "image.download.result",
            f"Image download failed: {error_type}",
            result="failure",
            status="missing",
            error_code=f"IMAGE_{error_type.upper()}",
            error_message=str(item.get("error_message") or error_type),
            retryable=bool(item.get("retryable")),
            attempt=int(item.get("attempts") or 1),
            duration_ms=float(item.get("duration_ms") or 0.0),
            operation="image_download",
            tags=["image", "download"],
            job_id=job_id,
            tid=tid,
            payload=payload,
        )
    emit(
        LOG,
        logging.INFO if not failures and not stopped else logging.WARNING,
        "image.download.summary",
        "Image download stage completed",
        result="success" if not failures and not stopped else "partial",
        status="ok" if not failures and not stopped else "partial",
        error_code=None if not stopped else f"IMAGE_{result.stopped_reason.upper()}",
        error_message=None if not stopped else result.stopped_reason,
        retryable=any(bool(item.get("retryable")) for item in failures),
        operation="image_download",
        tags=["image", "download", "summary"],
        job_id=job_id,
        tid=tid,
        payload={
            "attempted": len(result.diagnostics),
            "succeeded": len(result.diagnostics) - len(failures),
            "failed": len(failures),
            "stopped_reason": result.stopped_reason,
            "transports": sorted({str(item.get("transport")) for item in result.diagnostics}),
        },
    )


def _suffix_from_bytes(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return ".webp"
    return None


def _is_shared_forum_asset(image_url: str) -> bool:
    parsed = urlparse(image_url)
    return parsed.path.startswith("/static/image/")


def _is_embedded_image_url(image_url: str) -> bool:
    parsed = urlparse(image_url)
    lower = image_url.strip().lower()
    return lower.startswith("data:") or (parsed.scheme in {"http", "https"} and parsed.netloc.lower().startswith("data:"))


def _shared_target_path(paths: StoragePaths, image_url: str) -> Path:
    target = paths.shared_asset_path(image_url)
    if target.suffix:
        return target
    return target.with_suffix(_guess_suffix(image_url))


def _is_exportable_content_image(snapshot: ThreadSnapshot, floor) -> bool:
    if snapshot.publisher and floor.publisher:
        return floor.publisher == snapshot.publisher
    return floor.floor_no == 1


def _should_exclude_from_export(target: Path, *, image_url: str) -> bool:
    path_lower = urlparse(image_url).path.lower()
    if "/static/image/" in path_lower or "/hrline/" in path_lower:
        return True
    size = _read_image_size(target)
    if size is None:
        return False
    width, height = size
    if width <= 0 or height <= 0:
        return False
    if width <= 160 and height <= 160:
        return True
    if height <= 32 and width <= 480:
        return True
    return False


def _read_image_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as fp:
            header = fp.read(64)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                width = int.from_bytes(header[16:20], "big")
                height = int.from_bytes(header[20:24], "big")
                return width, height
            if header.startswith((b"GIF87a", b"GIF89a")) and len(header) >= 10:
                width = int.from_bytes(header[6:8], "little")
                height = int.from_bytes(header[8:10], "little")
                return width, height
            if header.startswith(b"\xff\xd8"):
                return _read_jpeg_size(fp)
            if header.startswith(b"RIFF") and len(header) >= 16 and header[8:12] == b"WEBP":
                return _read_webp_size(fp, header)
    except OSError:
        return None
    return None


def _read_webp_size(fp, header: bytes) -> tuple[int, int] | None:
    """Read the dimensions from the common WebP chunk forms."""
    fp.seek(12)
    chunk = fp.read(8)
    if len(chunk) != 8:
        return None
    kind = chunk[:4]
    size = int.from_bytes(chunk[4:8], "little")
    payload = fp.read(min(size, 32))
    if kind == b"VP8X" and len(payload) >= 10:
        width = 1 + int.from_bytes(payload[4:7], "little")
        height = 1 + int.from_bytes(payload[7:10], "little")
        return width, height
    if kind == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
        bits = int.from_bytes(payload[1:5], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    # Lossy VP8 has a frame start code followed by a 16-bit width/height.
    marker = payload.find(b"\x9d\x01\x2a")
    if marker >= 0 and len(payload) >= marker + 7:
        width = int.from_bytes(payload[marker + 3:marker + 5], "little") & 0x3FFF
        height = int.from_bytes(payload[marker + 5:marker + 7], "little") & 0x3FFF
        return width, height
    return None


def _is_valid_image_file(path: Path) -> bool:
    try:
        file_size = path.stat().st_size
        if file_size <= 64:
            return False
        with path.open("rb") as fp:
            header = fp.read(64)
            fp.seek(max(file_size - 64, 0))
            tail = fp.read(64)
        suffix = _suffix_from_bytes(header)
        if suffix is None:
            return False
        if suffix == ".jpg" and not _jpeg_has_end_marker_near_tail(tail):
            return False
        if suffix == ".png" and not tail.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            return False
        if suffix == ".gif" and not tail.endswith(b";"):
            return False
        if suffix == ".webp":
            declared_size = int.from_bytes(header[4:8], "little") + 8
            if declared_size > file_size:
                return False
        size = _read_image_size(path)
        return bool(size and size[0] > 0 and size[1] > 0)
    except (OSError, ValueError):
        return False


def _jpeg_has_end_marker_near_tail(tail: bytes, *, max_trailing_bytes: int = 64) -> bool:
    """Accept JPEG padding emitted by some attachment edges after EOI.

    A JPEG decoder stops at EOI, and the live attachment service has been
    observed to append a few bytes after that marker while still returning a
    decodable image.  Requiring EOI to be the final two bytes turns those
    valid responses into false ``truncated_image`` failures.  A bounded tail
    allowance keeps genuinely incomplete bodies (which have no EOI) invalid.
    """
    marker = tail.rfind(b"\xff\xd9")
    return marker >= 0 and len(tail) - marker - 2 <= max_trailing_bytes


def _require_valid_image_file(path: Path) -> None:
    if not _is_valid_image_file(path):
        raise ValueError(f"invalid or incomplete image: {path.name}")


def _read_jpeg_size(fp) -> tuple[int, int] | None:
    fp.seek(0)
    if fp.read(2) != b"\xff\xd8":
        return None
    while True:
        marker_prefix = fp.read(1)
        if not marker_prefix:
            return None
        if marker_prefix != b"\xff":
            continue
        marker = fp.read(1)
        while marker == b"\xff":
            marker = fp.read(1)
        if not marker:
            return None
        if marker in {b"\xd8", b"\xd9"}:
            continue
        if marker == b"\x01" or b"\xd0" <= marker <= b"\xd7":
            continue
        segment_length_bytes = fp.read(2)
        if len(segment_length_bytes) != 2:
            return None
        segment_length = int.from_bytes(segment_length_bytes, "big")
        if segment_length < 2:
            return None
        if marker in {b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf"}:
            payload = fp.read(segment_length - 2)
            if len(payload) < 5:
                return None
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            return width, height
        fp.seek(segment_length - 2, 1)
