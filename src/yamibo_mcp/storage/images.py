from __future__ import annotations

import shutil
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from mimetypes import guess_extension
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlparse

from yamibo_mcp.domain.models import ThreadSnapshot
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.yamibo.runtime_limits import acquire_cookie_download_slot


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
    referer: str | None = None,
    on_progress: Callable[[], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> ImageDownloadResult:
    with acquire_cookie_download_slot(cookie_file):
        staging_dir = paths.staging_job_images_dir(job_id)
        staging_dir.mkdir(parents=True, exist_ok=True)
        downloaded_relpaths: dict[int, list[str]] = {}
        non_export_relpaths: dict[int, list[str]] = {}
        shared_relpaths: dict[int, list[str]] = {}
        skipped_relpaths: dict[int, list[str]] = {}
        missing_urls: list[str] = []
        missing_shared_urls: list[str] = []
        handlers = [urllib.request.HTTPCookieProcessor(cookie_jar or CookieJar())]
        if not use_system_proxy:
            handlers.insert(0, urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)

        for floor in snapshot.floors:
            if cancel_check is not None:
                cancel_check()
            floor_relpaths: list[str] = []
            floor_non_export_relpaths: list[str] = []
            floor_shared_relpaths: list[str] = []
            floor_skipped: list[str] = []
            for index, image_url in enumerate(floor.image_urls, start=1):
                if cancel_check is not None:
                    cancel_check()
                if _is_embedded_image_url(image_url):
                    floor_skipped.append(image_url)
                    if on_progress is not None:
                        on_progress()
                    continue
                if _is_shared_forum_asset(image_url):
                    shared_target = _shared_target_path(paths, image_url)
                    try:
                        if not shared_target.exists():
                            _download_to_explicit_target_with_retries(
                                image_url,
                                target=shared_target,
                                timeout=timeout,
                                retries=retries,
                                opener=opener,
                                headers=headers,
                                referer=referer,
                                cancel_check=cancel_check,
                            )
                    except Exception:  # noqa: BLE001 - 共享表情失败不影响帖子完整性
                        missing_shared_urls.append(image_url)
                        if on_progress is not None:
                            on_progress()
                        continue
                    floor_shared_relpaths.append(str(shared_target.relative_to(paths.data_dir)))
                    if on_progress is not None:
                        on_progress()
                    continue

                if not _is_exportable_content_image(snapshot, floor):
                    floor_skipped.append(image_url)
                    if on_progress is not None:
                        on_progress()
                    continue

                stem = f"floor_{floor.floor_no:03d}_{index:02d}"
                try:
                    target = _download_with_retries(
                        image_url,
                        staging_dir=staging_dir,
                        stem=stem,
                        timeout=timeout,
                        retries=retries,
                        opener=opener,
                        headers=headers,
                        referer=referer,
                        cancel_check=cancel_check,
                    )
                except Exception:  # noqa: BLE001 - 图片失败先记录，不阻断整帖同步
                    missing_urls.append(image_url)
                    if on_progress is not None:
                        on_progress()
                    continue
                relative = f"images/{target.name}"
                if _should_exclude_from_export(target, image_url=image_url):
                    floor_non_export_relpaths.append(relative)
                else:
                    floor_relpaths.append(relative)
                if on_progress is not None:
                    on_progress()
            if floor_relpaths:
                downloaded_relpaths[floor.pid] = floor_relpaths
            if floor_non_export_relpaths:
                non_export_relpaths[floor.pid] = floor_non_export_relpaths
            if floor_shared_relpaths:
                shared_relpaths[floor.pid] = floor_shared_relpaths
            if floor_skipped:
                skipped_relpaths[floor.pid] = floor_skipped
            if on_progress is not None and not floor.image_urls:
                on_progress()

        return ImageDownloadResult(
            downloaded_relpaths=downloaded_relpaths,
            non_export_relpaths=non_export_relpaths,
            shared_relpaths=shared_relpaths,
            skipped_relpaths=skipped_relpaths,
            downloaded_count=sum(len(paths) for paths in downloaded_relpaths.values()),
            non_export_count=sum(len(paths) for paths in non_export_relpaths.values()),
            shared_downloaded_count=sum(len(paths) for paths in shared_relpaths.values()),
            missing_urls=missing_urls,
            missing_shared_urls=missing_shared_urls,
        )


def materialize_staged_images(paths: StoragePaths, job_id: str, tid: int) -> None:
    staging_dir = paths.staging_job_images_dir(job_id)
    if not staging_dir.exists():
        return
    thread_images_dir = paths.thread_images_dir(tid)
    thread_images_dir.mkdir(parents=True, exist_ok=True)
    # 先在 staging 下载，再复制到正式归档目录，方便后续继续演进成更严格的 finalize 检查。
    for path in staging_dir.iterdir():
        if path.is_file():
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
) -> Path:
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _fetch_to_path(
                image_url,
                staging_dir=staging_dir,
                stem=stem,
                timeout=timeout,
                opener=opener,
                headers=headers,
                referer=referer,
                cancel_check=cancel_check,
            )
        except Exception as exc:  # noqa: BLE001 - 上层只关心最终是否成功
            last_error = exc
            if attempt + 1 >= attempts:
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
) -> None:
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            _fetch_to_explicit_target(
                image_url,
                target=target,
                timeout=timeout,
                opener=opener,
                headers=headers,
                referer=referer,
                cancel_check=cancel_check,
            )
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(min(0.5 * (attempt + 1), 2.0))
    assert last_error is not None
    raise last_error


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
) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(image_url)
    # 本地样例页里的 file:// 图片直接复制；远端 http(s) 图片通过 urllib 下载。
    if parsed.scheme == "file":
        if cancel_check is not None:
            cancel_check()
        source = Path(urllib.request.url2pathname(parsed.path))
        target = staging_dir / f"{stem}{_guess_suffix(image_url)}"
        shutil.copy2(source, target)
        if cancel_check is not None:
            cancel_check()
        return target
    if parsed.scheme in {"http", "https"}:
        request_headers = dict(headers or {})
        if referer:
            request_headers.setdefault("Referer", referer)
        request = urllib.request.Request(image_url, headers=request_headers)
        with opener.open(request, timeout=timeout) as response:
            first_bytes = response.read(64)
            if cancel_check is not None:
                cancel_check()
            suffix = _suffix_from_response(response, image_url=image_url, first_bytes=first_bytes)
            target = staging_dir / f"{stem}{suffix}"
            with target.open("wb") as fp:
                if first_bytes:
                    fp.write(first_bytes)
                while True:
                    if cancel_check is not None:
                        cancel_check()
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)
        return target
    source = Path(image_url)
    if source.exists():
        if cancel_check is not None:
            cancel_check()
        target = staging_dir / f"{stem}{source.suffix or '.bin'}"
        shutil.copy2(source, target)
        if cancel_check is not None:
            cancel_check()
        return target
    raise FileNotFoundError(f"unsupported image source: {image_url}")


def _fetch_to_explicit_target(
    image_url: str,
    *,
    target: Path,
    timeout: float,
    opener,
    headers: Mapping[str, str] | None = None,
    referer: str | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(image_url)
    if parsed.scheme == "file":
        if cancel_check is not None:
            cancel_check()
        source = Path(urllib.request.url2pathname(parsed.path))
        shutil.copy2(source, target)
        if cancel_check is not None:
            cancel_check()
        return
    if parsed.scheme in {"http", "https"}:
        request_headers = dict(headers or {})
        if referer:
            request_headers.setdefault("Referer", referer)
        request = urllib.request.Request(image_url, headers=request_headers)
        with opener.open(request, timeout=timeout) as response, target.open("wb") as fp:
            while True:
                if cancel_check is not None:
                    cancel_check()
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                fp.write(chunk)
        return
    source = Path(image_url)
    if source.exists():
        if cancel_check is not None:
            cancel_check()
        shutil.copy2(source, target)
        if cancel_check is not None:
            cancel_check()
        return
    raise FileNotFoundError(f"unsupported image source: {image_url}")


def _suffix_from_response(response, *, image_url: str, first_bytes: bytes = b"") -> str:
    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        mapped = guess_extension(content_type)
        if mapped == ".jpe":
            return ".jpg"
        if mapped:
            return mapped
    sniffed = _suffix_from_bytes(first_bytes)
    if sniffed is not None:
        return sniffed
    final_url = getattr(response, "geturl", lambda: image_url)()
    suffix = _guess_suffix(final_url)
    if suffix != ".bin":
        return suffix
    return _guess_suffix(image_url)


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
    except OSError:
        return None
    return None


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
