from __future__ import annotations

import logging
import json
import time
from contextlib import ExitStack
from dataclasses import replace
from typing import Any

from yamibo_mcp.application.update_queries import check_thread_updates
from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.daemon.handlers.sync_thread import _check_cancelled, _check_paused, _merge_thread_snapshots
from yamibo_mcp.daemon.heartbeat import HeartbeatPacer
from yamibo_mcp.domain.content import build_content_snapshot
from yamibo_mcp.domain.models import FloorSnapshot, Job, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.domain.thread_fingerprint import floor_content_hash
from yamibo_mcp.domain.validation import validate_thread_snapshot
from yamibo_mcp.storage.images import download_images_to_staging
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.errors import ThreadPermissionRequiredError
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, has_configured_account_pool, next_permission_threshold
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.thread_detail import extract_author_only_total_pages, parse_thread_snapshot
from yamibo_mcp.yamibo.proxy_pool import select_thread_proxy

LOG = logging.getLogger(__name__)

def handle_update_thread(repo: JobsRepository, job: Job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
    paths = StoragePaths(
        settings.data_dir,
        export_dir=settings.export_dir,
        novel_txt_export_dir=settings.novel_txt_export_dir,
    )
    tid = job.tid or job.payload.get("tid")
    if tid is None:
        raise ValueError("update_thread requires tid")
    tid = int(tid)

    base_url = job.payload.get("base_url")
    check_result = check_thread_updates(tid=tid, base_url=base_url)
    status = str(check_result.get("status") or "")
    if status == "not_supported":
        raise ValueError(str(check_result.get("reason") or f"thread {tid} is not supported for incremental update"))
    if status == "failed":
        raise ValueError(str(check_result.get("reason") or f"failed to inspect thread {tid} before update"))
    if status == "up_to_date":
        repo.update_stage(job.job_id, "finalize", progress_current=1, progress_total=1)
        repo.succeed(job.job_id, {"tid": tid, "updated": False, "status": status, "reason": check_result.get("reason")})
        return
    if status != "updated":
        raise ValueError(str(check_result.get("reason") or f"unexpected update check status: {status}"))

    thread_repo = ThreadsRepository(repo.conn)
    thread = thread_repo.get_thread(tid)
    if thread is None:
        raise ValueError(f"thread not found: {tid}")

    archive_signature = (check_result.get("local_snapshot") or {}).get("archive_signature") or {}
    local_total_pages = archive_signature.get("author_only_total_pages") or archive_signature.get("total_pages_detected")
    if not local_total_pages:
        raise ValueError("missing local author-only page count; full resync required")
    local_total_pages = int(local_total_pages)
    if local_total_pages <= 0:
        raise ValueError("invalid local author-only page count; full resync required")

    local_snapshot = _load_local_thread_snapshot(paths, repo.conn, thread)
    if local_snapshot.publisher_uid is None:
        raise ValueError(f"missing publisher_uid for thread {tid}")

    forum_id = int(thread["forum_id"]) if "forum_id" in thread.keys() and thread["forum_id"] is not None else None
    resolved_base_url = str(base_url) if base_url else _base_url_for_thread(thread)
    client_stack = ExitStack()

    proxy_binding = select_thread_proxy(settings, tid=tid, job_id=job.job_id)
    proxy_url = proxy_binding.proxy_url if proxy_binding else None
    proxy_pool_artifacts: dict[str, object] = {}
    if proxy_binding:
        proxy_pool_artifacts = {
            "proxy_pool.enabled": True,
            "proxy_pool.group": proxy_binding.group,
            "proxy_pool.node": proxy_binding.node,
            "proxy_pool.best_effort": proxy_binding.best_effort,
            "proxy_pool.diagnostics": proxy_binding.diagnostics,
        }
    elif getattr(settings, "proxy_pool", None) and settings.proxy_pool.enabled:
        proxy_pool_artifacts = {
            "proxy_pool.enabled": True,
            "proxy_pool.fallback": True,
            "proxy_pool.error": "no_usable_nodes",
        }

    try:
        stack = client_stack
        if has_configured_account_pool(settings):
            min_permission: int | None = None
            identity = None
            while True:
                try:
                    identity, client = stack.enter_context(borrow_yamibo_client(settings, min_permission=min_permission, proxy_url=proxy_url))
                    LOG.info(
                        "update_thread job=%s fetching with account_id=%s permission_level=%s cookie_file=%s min_permission=%s",
                        job.job_id,
                        getattr(identity, "account_id", "unknown"),
                        getattr(identity, "permission_level", "unknown"),
                        getattr(identity, "cookie_file", "unknown"),
                        min_permission,
                    )

                    repo.update_stage(job.job_id, "fetch_tail", progress_current=1, progress_total=4)
                    _check_cancelled(repo, job.job_id)
                    _check_paused(repo, job.job_id)
                    tail_page = client.fetch_thread_page(
                        tid=tid,
                        page=local_total_pages,
                        author_uid=str(local_snapshot.publisher_uid),
                        base_url=resolved_base_url,
                    )
                    tail_snapshot = parse_thread_snapshot(tail_page.html, url=tail_page.final_url, tid=tid)
                    remote_tail = tail_snapshot.floors[-1] if tail_snapshot.floors else None
                    if remote_tail is None:
                        raise ValueError("remote author-only tail page has no floors")
                    break
                except ThreadPermissionRequiredError as exc:
                    if min_permission is not None:
                        raise
                    next_min_permission = next_permission_threshold(exc.required_permission)
                    account_id = getattr(identity, "account_id", "unknown")
                    LOG.info(
                        "update_thread job=%s switching account after permission gate account_id=%s required_permission=%s next_min_permission=%s",
                        job.job_id,
                        account_id,
                        exc.required_permission,
                        next_min_permission,
                    )
                    stack.close()
                    client_stack = ExitStack()
                    stack = client_stack
                    min_permission = next_min_permission
        else:
            cookie_path = settings.cookie_file
            if not cookie_path.exists():
                fallback = settings.data_dir / "cookies.txt"
                cookie_path = fallback if fallback.exists() else cookie_path
            client = YamiboClient(
                timeout=getattr(settings, "request_timeout_seconds", 15.0),
                cookie_file=str(cookie_path),
                use_system_proxy=settings.use_system_proxy,
                proxy_url=proxy_url,
                login_username=settings.login_username,
                login_password=settings.login_password,
                request_interval=settings.request_interval_seconds,
                request_interval_jitter=settings.request_interval_jitter_seconds,
            )
            repo.update_stage(job.job_id, "fetch_tail", progress_current=1, progress_total=4)
            _check_cancelled(repo, job.job_id)
            _check_paused(repo, job.job_id)
            tail_page = client.fetch_thread_page(
                tid=tid,
                page=local_total_pages,
                author_uid=str(local_snapshot.publisher_uid),
                base_url=resolved_base_url,
            )
            tail_snapshot = parse_thread_snapshot(tail_page.html, url=tail_page.final_url, tid=tid)
            remote_tail = tail_snapshot.floors[-1] if tail_snapshot.floors else None
            if remote_tail is None:
                raise ValueError("remote author-only tail page has no floors")

        local_tail_pid = archive_signature.get("last_pid")
        local_tail_hash = archive_signature.get("last_floor_hash")
        remote_tail_hash = floor_content_hash(remote_tail.content)
        if remote_tail.pid != local_tail_pid or remote_tail_hash != local_tail_hash:
            raise ValueError("local tail page no longer matches remote content; full resync required")

        remote_total_pages = check_result.get("remote_snapshot", {}).get("total_pages")
        if remote_total_pages is None:
            remote_total_pages = extract_author_only_total_pages(
                tail_page.html,
                tid=tid,
                author_uid=str(local_snapshot.publisher_uid),
            )
        if remote_total_pages is None:
            raise ValueError("unable to determine remote author-only pagination")
        remote_total_pages = int(remote_total_pages)
        if remote_total_pages < local_total_pages:
            raise ValueError("remote author-only page count regressed; full resync required")

        if remote_total_pages == local_total_pages:
            repo.update_stage(job.job_id, "finalize", progress_current=4, progress_total=4)
            repo.succeed(
                job.job_id,
                {
                    "tid": tid,
                    "updated": False,
                    "status": "up_to_date",
                    "local_total_pages": local_total_pages,
                    "remote_total_pages": remote_total_pages,
                    "reason": "remote author-only snapshot already matches local archive",
                },
            )
            return

        remaining_pages = remote_total_pages - local_total_pages
        max_pages = max(int(settings.novel_author_only_max_pages), 1)
        if remaining_pages > max_pages:
            raise ValueError(
                f"remote author-only update has {remaining_pages} new pages, exceeds max_pages={max_pages}; full resync required"
            )

        repo.update_stage(job.job_id, "fetch_append", progress_current=2, progress_total=4)
        _check_cancelled(repo, job.job_id)
        _check_paused(repo, job.job_id)
        page_results = [tail_page]
        for page in range(local_total_pages + 1, remote_total_pages + 1):
            if settings.novel_author_only_page_delay_seconds > 0:
                time.sleep(settings.novel_author_only_page_delay_seconds)
            _check_paused(repo, job.job_id)
            page_results.append(
                client.fetch_thread_page(
                    tid=tid,
                    page=page,
                    author_uid=str(local_snapshot.publisher_uid),
                    base_url=resolved_base_url,
                )
            )

        page_snapshots = [
            parse_thread_snapshot(result.html, url=result.final_url, tid=tid)
            for result in page_results
        ]
        merged_snapshot = _merge_thread_snapshots([local_snapshot, *page_snapshots[1:]])
        validation = validate_thread_snapshot(merged_snapshot)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))

        existing_assets = AssetsRepository(repo.conn).list_assets(tid)
        existing_asset_map = {
            row["remote_url"]: {
                "local_path": row["local_path"] if "local_path" in row.keys() else None,
                "status": row["status"] if "status" in row.keys() else "pending",
            }
            for row in existing_assets
        }
        existing_meta = _load_local_metadata(paths, tid)
        existing_archive_maps = _extract_archive_maps(existing_meta)

        new_snapshot = replace(merged_snapshot, floors=merged_snapshot.floors[len(local_snapshot.floors):])
        repo.update_stage(job.job_id, "download_images", progress_current=3, progress_total=4)
        _check_cancelled(repo, job.job_id)
        _check_paused(repo, job.job_id)
        download_stage_timeout_seconds = float(getattr(settings, "image_download_stage_timeout_seconds", 600.0))
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
            _cancel_check()
            _check_paused(repo, job.job_id)

        image_result = download_images_to_staging(
            paths,
            job.job_id,
            new_snapshot,
            timeout=settings.image_download_timeout_seconds,
            retries=settings.image_download_retries,
            headers=client.headers,
            cookie_jar=client.cookie_jar,
            cookie_file=getattr(client, "cookie_file", None),
            use_system_proxy=client.use_system_proxy,
            proxy_url=getattr(client, "proxy_url", None),
            referer=tail_page.final_url,
            on_progress=_progress,
            cancel_check=_cancel_check,
            stage_deadline_seconds=download_stage_timeout_seconds,
        )
        _check_paused(repo, job.job_id)
        new_local_path_by_remote_url = _build_remote_local_path_map(new_snapshot, image_result)
        content = build_content_snapshot(merged_snapshot, forum_id=forum_id)
        synced_assets = [
            replace(
                asset,
                local_path=_resolve_asset_local_path(asset.remote_url, existing_asset_map, new_local_path_by_remote_url),
                status=_resolve_asset_status(asset.remote_url, asset.asset_type, existing_asset_map, new_local_path_by_remote_url, image_result),
            )
            for asset in content.assets
        ]
        all_blocks = [block for post in content.posts for block in post.blocks]

        merged_archive_maps = _merge_archive_maps(
            existing_archive_maps,
            image_result.downloaded_relpaths,
            image_result.non_export_relpaths,
            image_result.shared_relpaths,
            image_result.skipped_relpaths,
            image_result.missing_urls,
            image_result.missing_shared_urls,
        )
        archive_status = "partial" if (
            existing_meta.get("archive_status") == "partial"
            or image_result.missing_urls
            or image_result.missing_shared_urls
            or image_result.stopped_reason
            or existing_meta.get("missing_image_urls")
            or existing_meta.get("missing_shared_image_urls")
        ) else "complete"

        repo.update_stage(job.job_id, "db_commit", progress_current=4, progress_total=4)
        with transaction(repo.conn):
            ThreadsRepository(repo.conn).upsert_snapshot(
                merged_snapshot,
                forum_id=forum_id,
                category=thread["category"] if "category" in thread.keys() else None,
                context_path=str(paths.thread_context(tid).relative_to(settings.data_dir)),
                archive_status=archive_status,
                missing_image_urls=_unique_list(
                    [*(existing_meta.get("missing_image_urls") or []), *image_result.missing_urls]
                ),
            )
            ContentBlocksRepository(repo.conn).upsert_blocks(merged_snapshot.tid, all_blocks)
            AssetsRepository(repo.conn).upsert_assets(merged_snapshot.tid, synced_assets)

        repo.update_stage(job.job_id, "materialize", progress_current=4, progress_total=4)
        context_path, metadata_path = materialize_thread(
            paths,
            merged_snapshot,
            job_id=job.job_id,
            archived_images=merged_archive_maps["archived_images"],
            non_export_images=merged_archive_maps["non_export_images"],
            shared_images=merged_archive_maps["shared_images"],
            skipped_image_urls=merged_archive_maps["skipped_image_urls"],
            missing_image_urls=_unique_list(
                [*(existing_archive_maps["missing_image_urls"] or []), *image_result.missing_urls]
            ),
            missing_shared_image_urls=_unique_list(
                [*(existing_archive_maps["missing_shared_image_urls"] or []), *image_result.missing_shared_urls]
            ),
        )

        artifacts = {
            "tid": tid,
            "updated": True,
            "status": "updated",
            "local_total_pages": local_total_pages,
            "remote_total_pages": remote_total_pages,
            "pages_fetched": len(page_results),
            "page_urls": [result.final_url for result in page_results],
            "append_start_page": local_total_pages + 1,
            "append_end_page": remote_total_pages,
            "context_path": str(context_path),
            "metadata_path": str(metadata_path),
            "archive_status": archive_status,
            "archive_signature": {
                "author_uid": None if merged_snapshot.publisher_uid is None else str(merged_snapshot.publisher_uid),
                "last_pid": None if not merged_snapshot.floors else merged_snapshot.floors[-1].pid,
                "floor_count": len(merged_snapshot.floors),
                "last_floor_hash": None if not merged_snapshot.floors else floor_content_hash(merged_snapshot.floors[-1].content),
                "author_only_total_pages": remote_total_pages,
            },
            "downloaded_image_count": image_result.downloaded_count,
            "non_export_image_count": image_result.non_export_count,
            "shared_image_count": image_result.shared_downloaded_count,
            "missing_image_count": len(image_result.missing_urls),
            "missing_shared_image_count": len(image_result.missing_shared_urls),
            "download_stopped_reason": image_result.stopped_reason,
            "stopped_reason": image_result.stopped_reason,
        }
        artifacts.update(proxy_pool_artifacts)
        if image_result.missing_urls or image_result.missing_shared_urls or image_result.stopped_reason or archive_status == "partial":
            repo.partial(job.job_id, artifacts)
        else:
            repo.succeed(job.job_id, artifacts)
    finally:
        client_stack.close()


def _load_local_thread_snapshot(paths: StoragePaths, conn, thread_row) -> ThreadSnapshot:
    meta = _load_local_metadata(paths, int(thread_row["tid"]))
    if meta:
        return _snapshot_from_metadata(meta, conn, int(thread_row["tid"]))
    floors = [
        FloorSnapshot(
            pid=row["pid"],
            tid=int(thread_row["tid"]),
            floor_no=row["floor_no"],
            publisher=row["publisher"],
            content=row["content"] or "",
            pub_time=row["pub_time"],
            has_images=bool(row["has_images"]),
            publisher_uid=row["publisher_uid"] if "publisher_uid" in row.keys() else None,
            quote_text=row["quote_text"] if "quote_text" in row.keys() else None,
            reply_text=row["reply_text"] if "reply_text" in row.keys() else None,
        )
        for row in ThreadsRepository(conn).list_floors(int(thread_row["tid"]))
    ]
    title_row = ThreadsRepository(conn).get_title_parse(int(thread_row["tid"]))
    if title_row is None:
        raise ValueError(f"missing title parse for thread {thread_row['tid']}")
    title = TitleSnapshot(
        raw_title=thread_row["raw_title"],
        display_title=thread_row["display_title"] or thread_row["raw_title"],
        group_name=title_row["group_name"],
        author_guess=title_row["author_guess"],
        core_title_guess=title_row["core_title_guess"],
        normalized_core_title=title_row["normalized_core_title"],
        series_key=title_row["series_key"],
        title_aliases=json.loads(title_row["title_aliases_json"] or "[]"),
        chapter_name=title_row["chapter_name"],
        chapter_index=title_row["chapter_index"],
        chapter_index_end=title_row["chapter_index_end"],
        chapter_title=title_row["chapter_title"],
        subtitle=title_row["subtitle"],
        tags=json.loads(title_row["tags_json"] or "[]"),
        confidence=title_row["confidence"],
        needs_review=bool(title_row["needs_review"]),
        parser_version=title_row["parser_version"],
    )
    return ThreadSnapshot(
        tid=int(thread_row["tid"]),
        url=None,
        page_type=thread_row["page_type"] if "page_type" in thread_row.keys() else "thread_detail",
        raw_title=thread_row["raw_title"],
        display_title=thread_row["display_title"] or thread_row["raw_title"],
        title=title,
        publisher=thread_row["publisher"] if "publisher" in thread_row.keys() else None,
        publisher_uid=thread_row["publisher_uid"] if "publisher_uid" in thread_row.keys() else None,
        pub_time=thread_row["pub_time"] if "pub_time" in thread_row.keys() else None,
        permission=thread_row["permission"] if "permission" in thread_row.keys() else 0,
        floors=floors,
        image_count=thread_row["image_count"] if "image_count" in thread_row.keys() else 0,
    )


def _snapshot_from_metadata(meta: dict[str, Any], conn, tid: int) -> ThreadSnapshot:
    asset_urls_by_pid = _asset_urls_by_pid(conn, tid)
    title_meta = meta.get("title") or {}
    title = TitleSnapshot(
        raw_title=str(title_meta.get("raw_title") or meta.get("raw_title") or ""),
        display_title=str(title_meta.get("display_title") or meta.get("display_title") or meta.get("raw_title") or ""),
        group_name=title_meta.get("group_name"),
        author_guess=title_meta.get("author_guess"),
        core_title_guess=str(title_meta.get("core_title_guess") or meta.get("display_title") or meta.get("raw_title") or ""),
        normalized_core_title=str(title_meta.get("normalized_core_title") or title_meta.get("core_title_guess") or meta.get("display_title") or meta.get("raw_title") or ""),
        series_key=str(title_meta.get("series_key") or title_meta.get("normalized_core_title") or title_meta.get("core_title_guess") or meta.get("display_title") or meta.get("raw_title") or ""),
        title_aliases=list(title_meta.get("title_aliases") or []),
        chapter_name=title_meta.get("chapter_name"),
        chapter_index=title_meta.get("chapter_index"),
        chapter_index_end=title_meta.get("chapter_index_end"),
        chapter_title=title_meta.get("chapter_title"),
        subtitle=title_meta.get("subtitle"),
        tags=list(title_meta.get("tags") or []),
        confidence=float(title_meta.get("confidence") or 0.0),
        needs_review=bool(title_meta.get("needs_review")),
        parser_version=str(title_meta.get("parser_version") or "title-v1"),
    )
    floors = [
        _floor_snapshot_from_metadata(floor_meta, asset_urls_by_pid.get(int(floor_meta.get("pid") or 0), []))
        for floor_meta in meta.get("floors") or []
        if isinstance(floor_meta, dict)
    ]
    return ThreadSnapshot(
        tid=int(meta.get("tid") or 0),
        url=meta.get("url"),
        page_type=str(meta.get("page_type") or "thread_detail"),
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher=meta.get("publisher"),
        publisher_uid=meta.get("publisher_uid"),
        pub_time=meta.get("pub_time"),
        permission=int(meta.get("permission") or 0),
        floors=floors,
        image_count=int(meta.get("image_count") or 0),
    )


def _floor_snapshot_from_metadata(meta: dict[str, Any], recovered_image_urls: list[str] | None = None) -> FloorSnapshot:
    return FloorSnapshot(
        pid=int(meta.get("pid") or 0),
        tid=int(meta.get("tid") or 0),
        floor_no=int(meta.get("floor_no") or 0),
        publisher=meta.get("publisher"),
        content=str(meta.get("content") or ""),
        pub_time=meta.get("pub_time"),
        has_images=bool(recovered_image_urls) or bool(meta.get("has_images")),
        publisher_uid=meta.get("publisher_uid"),
        image_urls=list(recovered_image_urls or meta.get("image_urls") or []),
        quote_text=meta.get("quote_text"),
        reply_text=meta.get("reply_text"),
        rich_body_html=meta.get("rich_body_html"),
    )


def _load_local_metadata(paths: StoragePaths, tid: int) -> dict[str, Any]:
    meta_path = paths.thread_metadata(tid)
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _extract_archive_maps(meta: dict[str, Any]) -> dict[str, dict[int, list[str]] | list[str]]:
    return {
        "archived_images": _int_keyed_lists(meta.get("archived_images")),
        "non_export_images": _int_keyed_lists(meta.get("non_export_images")),
        "shared_images": _int_keyed_lists(meta.get("shared_images")),
        "skipped_image_urls": _int_keyed_lists(meta.get("skipped_image_urls")),
        "missing_image_urls": list(meta.get("missing_image_urls") or []),
        "missing_shared_image_urls": list(meta.get("missing_shared_image_urls") or []),
    }


def _asset_urls_by_pid(conn, tid: int) -> dict[int, list[str]]:
    rows = AssetsRepository(conn).list_assets(tid)
    grouped: dict[int, list[tuple[str | None, str]]] = {}
    for row in rows:
        pid = int(row["pid"])
        grouped.setdefault(pid, []).append(
            (
                row["local_path"] if "local_path" in row.keys() else None,
                row["remote_url"],
            )
        )
    asset_urls: dict[int, list[str]] = {}
    for pid, items in grouped.items():
        asset_urls[pid] = [remote_url for _, remote_url in sorted(items, key=lambda item: (item[0] or "", item[1]))]
    return asset_urls


def _int_keyed_lists(value: Any) -> dict[int, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, list[str]] = {}
    for key, items in value.items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(items, list):
            result[pid] = [str(item) for item in items]
    return result


def _build_remote_local_path_map(snapshot: ThreadSnapshot, image_result) -> dict[str, str]:
    local_path_by_remote_url: dict[str, str] = {}
    for floor in snapshot.floors:
        content_relpaths = image_result.downloaded_relpaths.get(floor.pid, [])
        non_export_relpaths = image_result.non_export_relpaths.get(floor.pid, [])
        all_relpaths = list(content_relpaths) + list(non_export_relpaths)
        for index, remote_url in enumerate(floor.image_urls):
            if index < len(all_relpaths):
                local_path_by_remote_url[remote_url] = all_relpaths[index]
    return local_path_by_remote_url


def _resolve_asset_local_path(
    remote_url: str,
    existing_asset_map: dict[str, dict[str, str | None]],
    new_local_path_by_remote_url: dict[str, str],
) -> str | None:
    existing = existing_asset_map.get(remote_url)
    if existing and existing.get("local_path"):
        return existing["local_path"]
    return new_local_path_by_remote_url.get(remote_url)


def _resolve_asset_status(
    remote_url: str,
    asset_type: str,
    existing_asset_map: dict[str, dict[str, str | None]],
    new_local_path_by_remote_url: dict[str, str],
    image_result,
) -> str:
    existing = existing_asset_map.get(remote_url)
    if existing and existing.get("status"):
        return str(existing["status"])
    if remote_url in image_result.missing_urls:
        return "missing"
    if remote_url in new_local_path_by_remote_url:
        return "downloaded"
    if asset_type == "shared":
        return "pending"
    return "pending"


def _merge_archive_maps(
    existing_maps: dict[str, dict[int, list[str]] | list[str]],
    downloaded_relpaths: dict[int, list[str]],
    non_export_relpaths: dict[int, list[str]],
    shared_relpaths: dict[int, list[str]],
    skipped_relpaths: dict[int, list[str]],
    missing_urls: list[str],
    missing_shared_urls: list[str],
) -> dict[str, dict[int, list[str]]]:
    return {
        "archived_images": _merge_pid_maps(existing_maps["archived_images"], downloaded_relpaths),
        "non_export_images": _merge_pid_maps(existing_maps["non_export_images"], non_export_relpaths),
        "shared_images": _merge_pid_maps(existing_maps["shared_images"], shared_relpaths),
        "skipped_image_urls": _merge_pid_maps(existing_maps["skipped_image_urls"], skipped_relpaths),
    }


def _merge_pid_maps(*maps: dict[int, list[str]]) -> dict[int, list[str]]:
    merged: dict[int, list[str]] = {}
    for mapping in maps:
        for pid, values in mapping.items():
            bucket = merged.setdefault(int(pid), [])
            for value in values:
                if value not in bucket:
                    bucket.append(value)
    return merged


def _unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _base_url_for_thread(thread_row) -> str:
    from urllib.parse import urlparse

    url = thread_row["url"] if "url" in thread_row.keys() else None
    if url:
        parsed = urlparse(str(url))
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return "https://bbs.yamibo.com"
