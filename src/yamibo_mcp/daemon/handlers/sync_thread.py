from __future__ import annotations

import logging
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.content import build_content_snapshot
from yamibo_mcp.domain.models import Job, ThreadSnapshot
from yamibo_mcp.domain.thread_fingerprint import floor_content_hash
from yamibo_mcp.domain.validation import validate_thread_snapshot
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.images import download_images_to_staging
from yamibo_mcp.storage.staging import write_staging_failure, write_staging_snapshot, write_staging_title_parse_log
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.services.title_hints import update_title_hints
from yamibo_mcp.services.title_llm import refine_title_parse_with_llm, title_parse_to_dict
from yamibo_mcp.daemon.heartbeat import HeartbeatPacer
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, has_configured_account_pool
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.thread_detail import parse_thread_snapshot
from yamibo_mcp.yamibo.urls import thread_page_url_from_tid
from yamibo_mcp.yamibo.urls import extract_tid_from_input

LOG = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class JobPaused(Exception):
    pass


def _check_cancelled(repo: JobsRepository, job_id: str) -> None:
    if repo.is_cancelled(job_id):
        raise JobCancelled(f"Job {job_id} was cancelled")


def _check_paused(repo: JobsRepository, job_id: str) -> None:
    if repo.is_paused(job_id):
        raise JobPaused(f"Job {job_id} was paused")


def handle_sync_thread(repo: JobsRepository, job: Job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
    paths = StoragePaths(
        settings.data_dir,
        export_dir=settings.export_dir,
        novel_txt_export_dir=settings.novel_txt_export_dir,
    )
    html_path_value = job.payload.get("html_path")
    url_value = job.payload.get("url")
    base_url = job.payload.get("base_url")
    forum_id = int(job.payload["forum_id"]) if job.payload.get("forum_id") is not None else None
    tid = job.tid or extract_tid_from_input(job.payload.get("tid", ""))
    source_url: str | None = None
    snapshot = None
    client: YamiboClient | None = None
    title_parse_log: dict[str, object] | None = None
    fetch_artifacts: dict[str, object] = {"archive_mode": "default"}

    try:
        with ExitStack() as stack:
            if html_path_value:
                html_path = Path(str(html_path_value))
                if not html_path.is_absolute():
                    html_path = settings.project_root / html_path
                html = html_path.read_text(encoding="utf-8", errors="ignore")
                source_url = str(url_value) if url_value else html_path.resolve().as_uri()
            else:
                # Worker 负责远端抓取；Server/Web 只负责把 tid/url 写进 job。
                cookie_path = settings.cookie_file
                if not cookie_path.exists():
                    fallback = settings.data_dir / "cookies.txt"
                    cookie_path = fallback if fallback.exists() else cookie_path
                if has_configured_account_pool(settings):
                    _, client = stack.enter_context(borrow_yamibo_client(settings))
                else:
                    client = YamiboClient(
                        timeout=getattr(settings, "request_timeout_seconds", 15.0),
                        cookie_file=str(cookie_path),
                        use_system_proxy=settings.use_system_proxy,
                        login_username=settings.login_username,
                        login_password=settings.login_password,
                        request_interval=settings.request_interval_seconds,
                        request_interval_jitter=settings.request_interval_jitter_seconds,
                    )
                author_uid_from_url = _extract_author_uid_from_url(str(url_value)) if url_value else None
                if tid is None:
                    raise ValueError("sync_thread requires tid")
                if author_uid_from_url:
                    fetched = client.fetch_thread_page(
                        tid=tid,
                        page=1,
                        author_uid=author_uid_from_url,
                        base_url=str(base_url) if base_url else None,
                    )
                    fetch_artifacts = {
                        "archive_mode": "novel_author_only",
                        "author_uid": author_uid_from_url,
                        "pages_fetched": 1,
                        "page_urls": [fetched.final_url],
                        "stopped_reason": "input_author_page",
                    }
                else:
                    fetched = client.fetch_thread(
                        tid=tid,
                        url=str(url_value) if url_value else None,
                        base_url=str(base_url) if base_url else None,
                    )
                html = fetched.html
                source_url = fetched.final_url

            repo.update_stage(job.job_id, "parse", progress_current=1, progress_total=6)
            _check_paused(repo, job.job_id)
            snapshot = parse_thread_snapshot(html, url=source_url, tid=tid)
            if forum_id is None:
                from yamibo_mcp.yamibo.parsers.thread_detail import extract_forum_id_from_html
                forum_id = extract_forum_id_from_html(html)
            from yamibo_mcp.yamibo.parsers.thread_detail import extract_category_from_html
            category = extract_category_from_html(html)
            _check_cancelled(repo, job.job_id)
            _check_paused(repo, job.job_id)
            if client is not None and forum_id == 55:
                if tid is None:
                    raise ValueError("novel author-only sync requires tid")
                author_uid = fetch_artifacts.get("author_uid") or snapshot.publisher_uid
                if not author_uid:
                    raise ValueError(f"unable to resolve publisher_uid for novel author-only thread {tid}")
                page_results, total_pages, stopped_reason = client.fetch_author_only_thread_pages(
                    tid=tid,
                    author_uid=str(author_uid),
                    base_url=str(base_url) if base_url else None,
                    max_pages=max(settings.novel_author_only_max_pages, 1),
                    page_delay_seconds=max(settings.novel_author_only_page_delay_seconds, 0.0),
                )
                page_snapshots = [
                    parse_thread_snapshot(result.html, url=result.final_url, tid=tid)
                    for result in page_results
                ]
                snapshot = _merge_thread_snapshots(page_snapshots)
                source_url = page_results[0].final_url
                fetch_artifacts = {
                    "archive_mode": "novel_author_only",
                    "author_uid": str(author_uid),
                    "pages_fetched": len(page_results),
                    "total_pages_detected": total_pages,
                    "page_urls": [result.final_url for result in page_results],
                    "stopped_reason": stopped_reason,
                    "unique_floors": len(snapshot.floors),
                }
                if snapshot.floors:
                    fetch_artifacts["archive_signature"] = {
                        "author_uid": str(author_uid),
                        "last_pid": snapshot.floors[-1].pid,
                        "floor_count": len(snapshot.floors),
                        "last_floor_hash": floor_content_hash(snapshot.floors[-1].content),
                        "author_only_total_pages": total_pages,
                    }
            elif client is not None:
                if tid is None:
                    raise ValueError("thread sync requires tid")
                page_results, total_pages, stopped_reason = client.fetch_thread_pages(
                    tid=tid,
                    base_url=str(base_url) if base_url else None,
                    max_pages=max(int(settings.archive_thread_max_pages), 1),
                    first_page=fetched,
                    page_delay_seconds=0.0,
                )
                page_snapshots = [
                    parse_thread_snapshot(result.html, url=result.final_url, tid=tid)
                    for result in page_results
                ]
                snapshot = _merge_thread_snapshots(page_snapshots)
                source_url = page_results[0].final_url
                fetch_artifacts = {
                    "archive_mode": "thread_pages",
                    "pages_fetched": len(page_results),
                    "total_pages_detected": total_pages,
                    "page_urls": [result.final_url for result in page_results],
                    "stopped_reason": stopped_reason,
                    "unique_floors": len(snapshot.floors),
                }
                if snapshot.floors:
                    fetch_artifacts["archive_signature"] = {
                        "author_uid": None if snapshot.publisher_uid is None else str(snapshot.publisher_uid),
                        "last_pid": snapshot.floors[-1].pid,
                        "floor_count": len(snapshot.floors),
                        "last_floor_hash": floor_content_hash(snapshot.floors[-1].content),
                        "total_pages_detected": total_pages,
                    }

            COMIC_NOVEL_FORUMS = {30, 55}
            if forum_id is not None and forum_id not in COMIC_NOVEL_FORUMS:
                from yamibo_mcp.domain.models import TitleSnapshot
                from yamibo_mcp.yamibo.title.normalizer import normalize_display_title

                display = normalize_display_title(snapshot.raw_title)
                snapshot = replace(
                    snapshot,
                    title=TitleSnapshot(
                        raw_title=snapshot.raw_title,
                        display_title=display,
                        group_name=None,
                        author_guess=snapshot.title.author_guess,
                        core_title_guess=display,
                        normalized_core_title=display,
                        series_key=None,
                        title_aliases=[],
                        chapter_name=None,
                        chapter_index=None,
                        chapter_index_end=None,
                        chapter_title=None,
                        subtitle=None,
                        tags=[],
                        confidence=1.0,
                        needs_review=False,
                        parser_version="no-extract",
                    ),
                )
                refined_title = snapshot.title
                llm_title_meta = None
            else:
                refined_title, llm_title_meta = refine_title_parse_with_llm(
                    settings,
                    raw_title=snapshot.raw_title,
                    parsed=replace(
                        snapshot.title,
                        parser_version=snapshot.title.parser_version,
                    ),
                )

            title_parse_log = {
                "tid": snapshot.tid,
                "source_url": source_url,
                "raw_title": snapshot.raw_title,
                "baseline": title_parse_to_dict(
                    replace(
                        snapshot.title,
                        parser_version=snapshot.title.parser_version,
                    )
                ),
                "final": title_parse_to_dict(refined_title),
                "llm": llm_title_meta,
            }
            write_staging_title_parse_log(paths, job.job_id, title_parse_log)
            if llm_title_meta is not None and bool(llm_title_meta.get("used")):
                snapshot = replace(
                    snapshot,
                    title=replace(
                        snapshot.title,
                        group_name=refined_title.group_name,
                        author_guess=refined_title.author_guess,
                        core_title_guess=refined_title.core_title_guess,
                        normalized_core_title=refined_title.normalized_core_title,
                        series_key=refined_title.series_key,
                        title_aliases=refined_title.title_aliases,
                        chapter_name=refined_title.chapter_name,
                        chapter_index=refined_title.chapter_index,
                        chapter_index_end=refined_title.chapter_index_end,
                        chapter_title=refined_title.chapter_title,
                        subtitle=refined_title.subtitle,
                        tags=refined_title.tags,
                        confidence=refined_title.confidence,
                        needs_review=refined_title.needs_review,
                        parser_version="title-v1+llm",
                    ),
                )
            elif llm_title_meta is not None:
                snapshot = replace(
                    snapshot,
                    title=replace(
                        snapshot.title,
                        parser_version="title-v1+llm-failed",
                    ),
                )
            LOG.info(
                "Thread %s title parsed llm_attempted=%s llm_used=%s series_key=%s chapter_name=%s chapter_title=%s",
                snapshot.tid,
                False if llm_title_meta is None else bool(llm_title_meta.get("attempted")),
                False if llm_title_meta is None else bool(llm_title_meta.get("used")),
                snapshot.title.series_key,
                snapshot.title.chapter_name,
                snapshot.title.chapter_title,
            )
            _check_paused(repo, job.job_id)
            repo.heartbeat(job.job_id, worker_id, lease_seconds)

            repo.update_stage(job.job_id, "staging", progress_current=2, progress_total=6)
            _check_paused(repo, job.job_id)
            # 先把解析出的快照写入 staging，后面即使校验或落库失败，也能留下排查材料。
            write_staging_snapshot(paths, job.job_id, snapshot)

            repo.update_stage(job.job_id, "validate", progress_current=3, progress_total=6)
            _check_paused(repo, job.job_id)
            validation = validate_thread_snapshot(snapshot)
            if not validation.valid:
                raise ValueError("; ".join(validation.errors))

            # 图片下载先走 staging，失败 URL 先记录下来，后续再演进成 partial 状态。
            _check_cancelled(repo, job.job_id)
            _check_paused(repo, job.job_id)
            repo.update_stage(job.job_id, "download_images", progress_current=4, progress_total=6)
            heartbeat_pacer = HeartbeatPacer(
                repo=repo,
                job_id=job.job_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                min_interval_seconds=max(float(getattr(settings, "worker_heartbeat_seconds", 15)), 1.0),
            )
            heartbeat_pacer.beat(force=True)
            def _cancel_check() -> None:
                _check_cancelled(repo, job.job_id)

            def _progress() -> None:
                heartbeat_pacer.beat()
                _check_paused(repo, job.job_id)
                _cancel_check()

            image_result = download_images_to_staging(
                paths,
                job.job_id,
                snapshot,
                timeout=settings.image_download_timeout_seconds,
                retries=settings.image_download_retries,
                headers=None if client is None else client.headers,
                cookie_jar=None if client is None else client.cookie_jar,
                cookie_file=None if client is None else getattr(client, "cookie_file", None),
                use_system_proxy=False if client is None else client.use_system_proxy,
                referer=source_url,
                on_progress=_progress,
                cancel_check=_cancel_check,
            )
            _check_paused(repo, job.job_id)

            repo.update_stage(job.job_id, "db_commit", progress_current=5, progress_total=6)
            context_path = paths.thread_context(snapshot.tid)
            relative_context = str(context_path.relative_to(settings.data_dir))
            archive_status = "partial" if image_result.missing_urls else "complete"
            content = build_content_snapshot(snapshot, forum_id=forum_id)
            status_by_remote_url = {asset.remote_url: "pending" for asset in content.assets}
            local_path_by_remote_url = {asset.remote_url: None for asset in content.assets}
            for floor in snapshot.floors:
                for index, remote_url in enumerate(floor.image_urls):
                    content_relpaths = image_result.downloaded_relpaths.get(floor.pid, [])
                    non_export_relpaths = image_result.non_export_relpaths.get(floor.pid, [])
                    all_relpaths = list(content_relpaths) + list(non_export_relpaths)
                    if index < len(all_relpaths):
                        local_path_by_remote_url[remote_url] = all_relpaths[index]
                        status_by_remote_url[remote_url] = "downloaded"
                    elif remote_url in image_result.missing_urls:
                        status_by_remote_url[remote_url] = "missing"
            synced_assets = [
                replace(
                    asset,
                    local_path=local_path_by_remote_url.get(asset.remote_url),
                    status=status_by_remote_url.get(asset.remote_url, asset.status),
                )
                for asset in content.assets
            ]
            all_blocks = [block for post in content.posts for block in post.blocks]
            with transaction(repo.conn):
                ThreadsRepository(repo.conn).upsert_snapshot(
                    snapshot,
                    forum_id=forum_id,
                    category=category,
                    context_path=relative_context,
                    archive_status=archive_status,
                    missing_image_urls=image_result.missing_urls,
                    title_warnings=None if title_parse_log is None else {
                        "title_parse_log": {
                            "llm_attempted": False if llm_title_meta is None else bool(llm_title_meta.get("attempted")),
                            "llm_used": False if llm_title_meta is None else bool(llm_title_meta.get("used")),
                            "strategy": None if llm_title_meta is None else llm_title_meta.get("strategy"),
                            "model": None if llm_title_meta is None else llm_title_meta.get("model"),
                            "error": None if llm_title_meta is None else llm_title_meta.get("error"),
                            "baseline": title_parse_log["baseline"],
                            "final": title_parse_log["final"],
                        }
                    },
                )
                ContentBlocksRepository(repo.conn).upsert_blocks(snapshot.tid, all_blocks)
                AssetsRepository(repo.conn).upsert_assets(snapshot.tid, synced_assets)

            repo.update_stage(job.job_id, "materialize", progress_current=6, progress_total=6)
            context_path, metadata_path = materialize_thread(
                paths,
                snapshot,
                job_id=job.job_id,
                archived_images=image_result.downloaded_relpaths,
                non_export_images=image_result.non_export_relpaths,
                shared_images=image_result.shared_relpaths,
                skipped_image_urls=image_result.skipped_relpaths,
                missing_image_urls=image_result.missing_urls,
                missing_shared_image_urls=image_result.missing_shared_urls,
            )
            artifacts = {
                "tid": snapshot.tid,
                "context_path": str(context_path),
                "metadata_path": str(metadata_path),
                "floors": len(snapshot.floors),
                "floor_count": len(snapshot.floors),
                "downloaded_image_count": image_result.downloaded_count,
                "non_export_image_count": image_result.non_export_count,
                "shared_image_count": image_result.shared_downloaded_count,
                "skipped_image_count": sum(len(paths) for paths in image_result.skipped_relpaths.values()),
                "missing_image_count": len(image_result.missing_urls),
                "missing_shared_image_count": len(image_result.missing_shared_urls),
                "archive_status": archive_status,
                "pages_fetched": fetch_artifacts.get("pages_fetched", 1),
                "stopped_reason": fetch_artifacts.get("stopped_reason"),
                "archive_breakdown": {
                    "downloaded_relpaths": image_result.downloaded_relpaths,
                    "non_export_relpaths": image_result.non_export_relpaths,
                    "shared_relpaths": image_result.shared_relpaths,
                    "skipped_relpaths": image_result.skipped_relpaths,
                    "missing_image_urls": image_result.missing_urls,
                    "missing_shared_image_urls": image_result.missing_shared_urls,
                },
                "archive_signature": {
                    "author_uid": None if snapshot.publisher_uid is None else str(snapshot.publisher_uid),
                    "last_pid": None if not snapshot.floors else snapshot.floors[-1].pid,
                    "floor_count": len(snapshot.floors),
                    "last_floor_hash": None if not snapshot.floors else floor_content_hash(snapshot.floors[-1].content),
                },
            }
            artifacts.update(fetch_artifacts)
            if llm_title_meta is not None:
                artifacts["title_llm"] = {
                    "attempted": bool(llm_title_meta.get("attempted")),
                    "used": bool(llm_title_meta.get("used")),
                    "model": llm_title_meta.get("model"),
                    "strategy": llm_title_meta.get("strategy"),
                    "error": llm_title_meta.get("error"),
                }
            if image_result.missing_urls:
                LOG.info(
                    "Thread %s archived as partial: downloaded=%s non_export=%s shared=%s skipped=%s missing=%s missing_shared=%s",
                    snapshot.tid,
                    image_result.downloaded_count,
                    image_result.non_export_count,
                    image_result.shared_downloaded_count,
                    sum(len(paths) for paths in image_result.skipped_relpaths.values()),
                    len(image_result.missing_urls),
                    len(image_result.missing_shared_urls),
                )
                if image_result.missing_urls:
                    LOG.warning(
                        "Thread %s missing image urls: %s",
                        snapshot.tid,
                        ", ".join(image_result.missing_urls[:20]),
                    )
                if image_result.missing_shared_urls:
                    LOG.warning(
                        "Thread %s missing shared image urls: %s",
                        snapshot.tid,
                        ", ".join(image_result.missing_shared_urls[:20]),
                    )
                update_title_hints(
                    settings,
                    group_name=snapshot.title.group_name,
                    author_guess=snapshot.title.author_guess,
                )
                repo.partial(job.job_id, artifacts)
            else:
                LOG.info(
                    "Thread %s archived successfully: downloaded=%s non_export=%s shared=%s skipped=%s",
                    snapshot.tid,
                    image_result.downloaded_count,
                    image_result.non_export_count,
                    image_result.shared_downloaded_count,
                    sum(len(paths) for paths in image_result.skipped_relpaths.values()),
                )
                update_title_hints(
                    settings,
                    group_name=snapshot.title.group_name,
                    author_guess=snapshot.title.author_guess,
                )
                repo.succeed(job.job_id, artifacts)
    except Exception as exc:
        write_staging_failure(
            paths,
            job.job_id,
            {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "stage": repo.get(job.job_id).stage,
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
                "source_url": source_url,
                "payload": job.payload,
                "snapshot_tid": None if snapshot is None else snapshot.tid,
            },
        )
        raise


def _extract_author_uid_from_url(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.query:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    author_uid = query.get("authorid", [None])[0]
    if author_uid in {None, ""}:
        return None
    return str(author_uid)


def _merge_thread_snapshots(snapshots: list[ThreadSnapshot]) -> ThreadSnapshot:
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    primary = snapshots[0]
    seen_pids: set[int] = set()
    merged_floors = []
    for snapshot in snapshots:
        for floor in snapshot.floors:
            if floor.pid in seen_pids:
                continue
            seen_pids.add(floor.pid)
            merged_floors.append(floor)
    reindexed_floors = [
        replace(floor, floor_no=index)
        for index, floor in enumerate(merged_floors, start=1)
    ]
    author_uid = next(
        (_extract_author_uid_from_url(str(snapshot.url or "")) for snapshot in snapshots if _extract_author_uid_from_url(str(snapshot.url or ""))),
        None,
    )
    return replace(
        primary,
        floors=reindexed_floors,
        image_count=sum(len(floor.image_urls) if floor.image_urls else int(floor.has_images) for floor in reindexed_floors),
        url=thread_page_url_from_tid(
            primary.tid,
            page=1,
            author_uid=author_uid,
            base_url=_base_url_for_snapshot(primary),
        ),
    )


def _base_url_for_snapshot(snapshot) -> str:
    if snapshot.url:
        parsed = urlparse(str(snapshot.url))
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return "https://bbs.yamibo.com"
