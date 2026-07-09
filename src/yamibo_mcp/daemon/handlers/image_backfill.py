from __future__ import annotations

import logging
import shutil
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.parse import unquote

from yamibo_mcp.application.thread_context import build_obsidian_context_payload
from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.content import build_content_snapshot
from yamibo_mcp.domain.models import FloorSnapshot, Job, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.images import download_images_to_staging
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.daemon.handlers.update_thread import _load_local_thread_snapshot
from yamibo_mcp.daemon.handlers.sync_thread import _check_cancelled, _check_paused
from yamibo_mcp.errors import ThreadPermissionRequiredError, RemoteMaintenanceError, UnexpectedPageError, _extract_permission_code
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, next_permission_threshold
from yamibo_mcp.yamibo.anti_bot import ensure_no_maintenance_pause
from yamibo_mcp.yamibo.parsers.thread_detail import parse_thread_snapshot
from yamibo_mcp.yamibo.proxy_pool import activate_proxy_binding, select_thread_proxy


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LocalAsset:
    remote_url: str
    asset_type: str
    local_path: str | None
    status: str | None


def handle_image_backfill(
    repo: JobsRepository,
    job: Job,
    worker_id: str,
    lease_seconds: int,
    settings: Settings,
) -> None:
    tid = int(job.tid or job.payload.get("tid") or 0)
    if tid <= 0:
        raise ValueError("image_backfill requires tid")

    dry_run = bool(job.payload.get("dry_run", True))

    max_pages = max(int(job.payload.get("max_pages") or getattr(settings, "image_backfill_max_pages", 1)), 1)
    base_url = str(job.payload.get("base_url") or "https://bbs.yamibo.com")
    fixed_after = job.payload.get("fixed_after") or getattr(settings, "image_backfill_fixed_after", None)
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir, novel_txt_export_dir=settings.novel_txt_export_dir)

    repo.update_stage(job.job_id, "load_local", progress_current=1, progress_total=4)
    thread = ThreadsRepository(repo.conn).get_thread(tid)
    if thread is None:
        raise ValueError(f"local archive not found for image_backfill: {tid}")
    local_assets = _local_assets_by_url(AssetsRepository(repo.conn).list_assets(tid))
    local_snapshot = _load_local_snapshot_for_backfill(paths, repo.conn, thread)
    archive_generation = _archive_generation(thread, fixed_after=fixed_after)
    proxy_binding = select_thread_proxy(settings, tid=tid, job_id=job.job_id)
    proxy_url = proxy_binding.proxy_url if proxy_binding else None
    proxy_pool_artifacts = _proxy_pool_artifacts(settings, proxy_binding)

    repo.update_stage(job.job_id, "fetch_remote", progress_current=2, progress_total=4)
    fetch_lease_seconds = max(
        lease_seconds,
        int(max(float(getattr(settings, "request_timeout_seconds", 30.0)) * 4.0, 120.0)),
    )
    repo.heartbeat(job.job_id, worker_id, fetch_lease_seconds)
    min_permission: int | None = None
    while True:
        identity = None
        borrowed_client = False
        try:
            _check_cancelled(repo, job.job_id)
            _check_paused(repo, job.job_id)
            ensure_no_maintenance_pause(repo.conn)

            with ExitStack() as stack:
                stack.enter_context(activate_proxy_binding(settings, proxy_binding))
                identity, client = stack.enter_context(
                    borrow_yamibo_client(settings, min_permission=min_permission, proxy_url=proxy_url)
                )
                borrowed_client = True
                first_page = client.fetch_thread_page(tid=tid, page=1, base_url=base_url)
                if max_pages > 1:
                    page_results, remote_total_pages, stopped_reason = client.fetch_thread_pages(
                        tid=tid,
                        base_url=base_url,
                        max_pages=max_pages,
                        first_page=first_page,
                        page_delay_seconds=max(float(getattr(settings, "request_interval_seconds", 1.0)), 1.0),
                    )
                    final_url = page_results[-1].final_url
                    pages_fetched = len(page_results)
                    snapshot = _merge_page_snapshots(
                        [parse_thread_snapshot(result.html, url=result.final_url, tid=tid) for result in page_results]
                    )
                else:
                    remote_total_pages = None
                    stopped_reason = "max_pages"
                    final_url = first_page.final_url
                    pages_fetched = 1
                    snapshot = parse_thread_snapshot(first_page.html, url=first_page.final_url, tid=tid)

                repo.update_stage(job.job_id, "diff_images", progress_current=3, progress_total=4)
                diff = _diff_snapshot_images(snapshot, local_assets=local_assets, paths=paths, include_shared_static=True)

                artifacts: dict[str, Any] = {
                    "dry_run": dry_run,
                    "tid": tid,
                    "base_url": base_url,
                    "remote_access_pattern": "direct_tid_thread_pages",
                    "account_id": identity.account_id,
                    "account_permission_level": identity.permission_level,
                    "account_min_permission": min_permission,
                    "pages_fetched": pages_fetched,
                    "max_pages": max_pages,
                    "remote_total_pages": remote_total_pages,
                    "stopped_reason": stopped_reason,
                    "final_url": final_url,
                    "archive_generation": archive_generation,
                    "archive_status_before": thread["archive_status"] if "archive_status" in thread.keys() else None,
                    "sync_time": _iso(thread["sync_time"]) if "sync_time" in thread.keys() else None,
                    **proxy_pool_artifacts,
                    **diff,
                }
                if dry_run:
                    repo.update_stage(job.job_id, "finalize", progress_current=4, progress_total=4)
                    repo.succeed(job.job_id, artifacts)
                    return

                apply_snapshot = _merge_remote_into_local(local_snapshot, snapshot)
                repo.update_stage(job.job_id, "download_missing_images", progress_current=4, progress_total=6)
                missing_snapshot = _snapshot_for_missing_images(snapshot, diff["missing_items_for_apply"])
                image_result = download_images_to_staging(
                    paths,
                    job.job_id,
                    missing_snapshot,
                    timeout=settings.image_download_timeout_seconds,
                    retries=settings.image_download_retries,
                    headers=client.headers,
                    cookie_jar=client.cookie_jar,
                    cookie_file=getattr(client, "cookie_file", None),
                    use_system_proxy=client.use_system_proxy,
                    proxy_url=getattr(client, "proxy_url", None),
                    referer=final_url,
                    stage_deadline_seconds=float(getattr(settings, "image_download_stage_timeout_seconds", 600.0)),
                )
                local_path_by_url = _build_downloaded_url_map(missing_snapshot, image_result)
                content = build_content_snapshot(apply_snapshot, forum_id=thread["forum_id"] if "forum_id" in thread.keys() else None)
                synced_assets = _merge_assets(content.assets, local_assets=local_assets, local_path_by_url=local_path_by_url, image_result=image_result)
                archive_maps = _archive_maps_from_assets(apply_snapshot, synced_assets)
                missing_image_urls = _unique([*image_result.missing_urls])
                missing_shared_image_urls = _unique([*image_result.missing_shared_urls])
                archive_status = "partial" if (missing_image_urls or missing_shared_image_urls or image_result.stopped_reason) else "complete"

                repo.update_stage(job.job_id, "db_commit", progress_current=5, progress_total=6)
                with transaction(repo.conn):
                    ThreadsRepository(repo.conn).upsert_snapshot(
                        apply_snapshot,
                        forum_id=thread["forum_id"] if "forum_id" in thread.keys() else None,
                        category=thread["category"] if "category" in thread.keys() else None,
                        context_path=str(paths.thread_context(tid).relative_to(settings.data_dir)),
                        archive_status=archive_status,
                        missing_image_urls=[*missing_image_urls, *missing_shared_image_urls],
                    )
                    ContentBlocksRepository(repo.conn).upsert_blocks(
                        apply_snapshot.tid,
                        [block for post in content.posts for block in post.blocks],
                    )
                    AssetsRepository(repo.conn).upsert_assets(apply_snapshot.tid, synced_assets)

                repo.update_stage(job.job_id, "materialize", progress_current=6, progress_total=6)
                context_metadata, cleaner_output = build_obsidian_context_payload(
                    apply_snapshot,
                    forum_id=thread["forum_id"] if "forum_id" in thread.keys() else None,
                    archive_status=archive_status,
                    reply_count=max(len(apply_snapshot.floors) - 1, 0),
                    sync_time=thread["sync_time"] if "sync_time" in thread.keys() else None,
                    archived_images=archive_maps["archived_images"],
                    non_export_images=archive_maps["non_export_images"],
                    shared_images=archive_maps["shared_images"],
                    skipped_image_urls=archive_maps["skipped_image_urls"],
                    missing_image_urls=missing_image_urls,
                    missing_shared_image_urls=missing_shared_image_urls,
                    context_source="image_backfill",
                )
                context_path, metadata_path = materialize_thread(
                    paths,
                    apply_snapshot,
                    job_id=job.job_id,
                    archived_images=archive_maps["archived_images"],
                    non_export_images=archive_maps["non_export_images"],
                    shared_images=archive_maps["shared_images"],
                    skipped_image_urls=archive_maps["skipped_image_urls"],
                    missing_image_urls=missing_image_urls,
                    missing_shared_image_urls=missing_shared_image_urls,
                    context_format_version="obsidian-md-v2",
                    cleaner_output=cleaner_output,
                    metadata=context_metadata,
                )
                artifacts.update(
                    {
                        "context_path": str(context_path),
                        "metadata_path": str(metadata_path),
                        "archive_status_after": archive_status,
                        "downloaded_image_count": image_result.downloaded_count,
                        "non_export_image_count": image_result.non_export_count,
                        "shared_image_count": image_result.shared_downloaded_count,
                        "skipped_image_count": sum(len(values) for values in image_result.skipped_relpaths.values()),
                        "missing_image_count": len(missing_image_urls),
                        "missing_shared_image_count": len(missing_shared_image_urls),
                        "download_stopped_reason": image_result.stopped_reason,
                        "backfilled_image_count": image_result.downloaded_count + image_result.non_export_count + image_result.shared_downloaded_count,
                    }
                )
                if archive_status == "partial":
                    repo.partial(job.job_id, artifacts)
                else:
                    repo.succeed(job.job_id, artifacts)
                # materialize 完成后清理 staging，避免磁盘膨胀。
                shutil.rmtree(paths.staging_job_dir(job.job_id), ignore_errors=True)
                return
        except ThreadPermissionRequiredError as exc:
            next_min_permission = next_permission_threshold(exc.required_permission, current_min=min_permission)
            LOG.info(
                "image_backfill job=%s switching account after permission gate account_id=%s required_permission=%s next_min_permission=%s",
                job.job_id,
                getattr(identity, "account_id", "unknown"),
                exc.required_permission,
                next_min_permission,
            )
            min_permission = next_min_permission
        except UnexpectedPageError as exc:
            code = _extract_permission_code(str(exc))
            page_type = getattr(exc, "details", {}).get("page_type", "")
            is_thread_missing = page_type in ("prompt_thread_missing_or_removed_or_review",)
            if (code is not None and code != 255) or is_thread_missing:
                next_min_permission = (min_permission or 0) + 1
                LOG.warning(
                    "image_backfill job=%s permission denied (code=%s) account_id=%s current_level=%s "
                    "min_permission=%s next_min_permission=%s; escalating",
                    job.job_id,
                    code,
                    getattr(identity, "account_id", "unknown"),
                    getattr(identity, "permission_level", "unknown"),
                    min_permission,
                    next_min_permission,
                )
                min_permission = next_min_permission
                continue
            raise
        except ValueError:
            if min_permission and not borrowed_client:
                LOG.warning(
                    "image_backfill job=%s no account with permission >= %s in pool (accounts: %s)",
                    job.job_id,
                    min_permission,
                    ", ".join(
                        f"{a.account_id}:{a.permission_level}"
                        for a in getattr(settings, "account_pool", [])
                        if getattr(a, "enabled", True)
                    ) or "(none)",
                )
                raise ThreadPermissionRequiredError(
                    f"no account with permission >= {min_permission} available in pool"
                ) from None
            raise


def _proxy_pool_artifacts(settings: Settings, proxy_binding) -> dict[str, object]:
    if proxy_binding:
        return {
            "proxy_pool.enabled": True,
            "proxy_pool.group": proxy_binding.group,
            "proxy_pool.node": proxy_binding.node,
            "proxy_pool.best_effort": proxy_binding.best_effort,
            "proxy_pool.diagnostics": proxy_binding.diagnostics,
        }
    if getattr(settings, "proxy_pool", None) and settings.proxy_pool.enabled:
        return {
            "proxy_pool.enabled": True,
            "proxy_pool.fallback": True,
            "proxy_pool.error": "no_usable_nodes",
        }
    return {}


def _local_assets_by_url(rows) -> dict[str, _LocalAsset]:
    assets: dict[str, _LocalAsset] = {}
    for row in rows:
        remote_url = str(row["remote_url"] or "")
        if not remote_url:
            continue
        assets[remote_url] = _LocalAsset(
            remote_url=remote_url,
            asset_type=str(row["asset_type"] or ""),
            local_path=row["local_path"] if "local_path" in row.keys() else None,
            status=row["status"] if "status" in row.keys() else None,
        )
    return assets


def _merge_page_snapshots(snapshots) -> Any:
    if not snapshots:
        raise ValueError("cannot merge empty image_backfill snapshot list")
    primary = snapshots[0]
    floors = []
    seen_pids: set[int] = set()
    for snapshot in snapshots:
        for floor in snapshot.floors:
            if floor.pid in seen_pids:
                continue
            seen_pids.add(floor.pid)
            floors.append(floor)
    floors.sort(key=lambda floor: (floor.floor_no, floor.pid))
    return replace(primary, floors=floors, image_count=sum(len(floor.image_urls) for floor in floors))


def _load_local_snapshot_for_backfill(paths: StoragePaths, conn, thread_row) -> ThreadSnapshot:
    try:
        return _load_local_thread_snapshot(paths, conn, thread_row)
    except ValueError as exc:
        if "missing title parse" not in str(exc):
            raise

    tid = int(thread_row["tid"])
    raw_title = str(thread_row["raw_title"] or "")
    display_title = str(thread_row["display_title"] or raw_title)
    title = TitleSnapshot(
        raw_title=raw_title,
        display_title=display_title,
        group_name=None,
        author_guess=None,
        core_title_guess=display_title or raw_title,
        normalized_core_title=display_title or raw_title,
        series_key=display_title or raw_title,
        title_aliases=[],
        chapter_name=None,
        chapter_index=None,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.0,
        needs_review=True,
    )
    floors = [
        FloorSnapshot(
            pid=row["pid"],
            tid=tid,
            floor_no=row["floor_no"],
            publisher=row["publisher"],
            content=row["content"] or "",
            pub_time=row["pub_time"],
            has_images=bool(row["has_images"]),
            publisher_uid=row["publisher_uid"] if "publisher_uid" in row.keys() else None,
            quote_text=row["quote_text"] if "quote_text" in row.keys() else None,
            reply_text=row["reply_text"] if "reply_text" in row.keys() else None,
        )
        for row in ThreadsRepository(conn).list_floors(tid)
    ]
    return ThreadSnapshot(
        tid=tid,
        url=None,
        page_type=thread_row["page_type"] if "page_type" in thread_row.keys() else "thread_detail",
        raw_title=raw_title,
        display_title=display_title,
        title=title,
        publisher=thread_row["publisher"] if "publisher" in thread_row.keys() else None,
        publisher_uid=thread_row["publisher_uid"] if "publisher_uid" in thread_row.keys() else None,
        pub_time=thread_row["pub_time"] if "pub_time" in thread_row.keys() else None,
        permission=thread_row["permission"] if "permission" in thread_row.keys() else 0,
        floors=floors,
        image_count=thread_row["image_count"] if "image_count" in thread_row.keys() else 0,
    )


def _merge_remote_into_local(local_snapshot, remote_snapshot):
    remote_by_pid = {floor.pid: floor for floor in remote_snapshot.floors}
    merged_floors = []
    seen_pids: set[int] = set()
    for floor in local_snapshot.floors:
        merged = remote_by_pid.get(floor.pid, floor)
        merged_floors.append(merged)
        seen_pids.add(floor.pid)
    for floor in remote_snapshot.floors:
        if floor.pid not in seen_pids:
            merged_floors.append(floor)
    merged_floors.sort(key=lambda floor: (floor.floor_no, floor.pid))
    return replace(
        remote_snapshot,
        floors=merged_floors,
        image_count=sum(len(floor.image_urls) if floor.image_urls else int(floor.has_images) for floor in merged_floors),
    )


def _diff_snapshot_images(
    snapshot,
    *,
    local_assets: dict[str, _LocalAsset],
    paths: StoragePaths,
    include_shared_static: bool = True,
) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    need_fetch_by_class: Counter[str] = Counter()
    content_need_fetch: list[dict[str, Any]] = []
    shared_static_missing: list[dict[str, Any]] = []
    missing_items_for_apply: list[dict[str, Any]] = []
    existing_ok = 0
    remote_image_count = 0
    remote_non_first_image_count = 0

    for floor in snapshot.floors:
        for url in floor.image_urls:
            remote_image_count += 1
            if floor.floor_no <= 1:
                continue
            remote_non_first_image_count += 1
            image_class = _classify_backfill_image(url)
            class_counts[image_class] += 1
            availability = _local_availability(
                tid=snapshot.tid,
                url=url,
                image_class=image_class,
                asset=local_assets.get(url),
                paths=paths,
            )
            if availability == "ok":
                existing_ok += 1
                continue
            reason_counts[availability] += 1
            need_fetch_by_class[image_class] += 1
            item = {
                "pid": floor.pid,
                "floor_no": floor.floor_no,
                "url": url,
                "class": image_class,
                "reason": availability,
            }
            if image_class == "content":
                content_need_fetch.append(item)
                missing_items_for_apply.append(item)
            else:
                shared_static_missing.append(item)
                if include_shared_static:
                    missing_items_for_apply.append(item)

    return {
        "remote_image_count": remote_image_count,
        "remote_non_first_image_count": remote_non_first_image_count,
        "existing_non_first_images_ok": existing_ok,
        "content_need_fetch_count": len(content_need_fetch),
        "shared_static_missing_count": len(shared_static_missing),
        "need_fetch_count": len(content_need_fetch),
        "need_fetch_by_class": dict(need_fetch_by_class),
        "remote_non_first_by_class": dict(class_counts),
        "need_fetch_reasons": dict(reason_counts),
        "apply_need_fetch_count": len(missing_items_for_apply),
        "missing_items_for_apply": missing_items_for_apply,
        "sample_content_need_fetch": content_need_fetch[:10],
        "sample_shared_static_missing": shared_static_missing[:10],
    }


def _snapshot_for_missing_images(snapshot, missing_items: list[dict[str, Any]]):
    urls_by_pid: dict[int, list[str]] = {}
    for item in missing_items:
        urls_by_pid.setdefault(int(item["pid"]), []).append(str(item["url"]))
    floors = [
        replace(floor, image_urls=urls_by_pid.get(floor.pid, []), has_images=bool(urls_by_pid.get(floor.pid)))
        for floor in snapshot.floors
        if floor.pid in urls_by_pid
    ]
    return replace(snapshot, floors=floors, image_count=sum(len(floor.image_urls) for floor in floors))


def _build_downloaded_url_map(snapshot, image_result) -> dict[str, str]:
    local_path_by_url: dict[str, str] = {}
    for floor in snapshot.floors:
        content_relpaths = [*image_result.downloaded_relpaths.get(floor.pid, []), *image_result.non_export_relpaths.get(floor.pid, [])]
        shared_relpaths = list(image_result.shared_relpaths.get(floor.pid, []))
        shared_index = 0
        for index, remote_url in enumerate(floor.image_urls, start=1):
            if _classify_backfill_image(remote_url) in {"static", "decorative"}:
                if shared_index < len(shared_relpaths):
                    local_path_by_url[remote_url] = shared_relpaths[shared_index]
                    shared_index += 1
                continue
            stem = f"floor_{floor.floor_no:03d}_{index:02d}"
            match = next((path for path in content_relpaths if Path(unquote(path)).name.startswith(stem)), None)
            if match is not None:
                local_path_by_url[remote_url] = match
    return local_path_by_url


def _merge_assets(remote_assets, *, local_assets: dict[str, _LocalAsset], local_path_by_url: dict[str, str], image_result):
    merged = []
    missing = set(image_result.missing_urls) | set(image_result.missing_shared_urls)
    for asset in remote_assets:
        existing = local_assets.get(asset.remote_url)
        local_path = existing.local_path if existing and existing.local_path else local_path_by_url.get(asset.remote_url)
        status = "downloaded" if local_path else ("missing" if asset.remote_url in missing else (existing.status if existing and existing.status else asset.status))
        merged.append(replace(asset, local_path=local_path, status=status))
    return merged


def _archive_maps_from_assets(snapshot, assets) -> dict[str, dict[int, list[str]]]:
    by_url = {asset.remote_url: asset for asset in assets}
    archived_images: dict[int, list[str]] = {}
    non_export_images: dict[int, list[str]] = {}
    shared_images: dict[int, list[str]] = {}
    skipped_image_urls: dict[int, list[str]] = {}
    for floor in snapshot.floors:
        for url in floor.image_urls:
            asset = by_url.get(url)
            if asset is None:
                continue
            if asset.local_path:
                if _classify_backfill_image(url) in {"static", "decorative"}:
                    shared_images.setdefault(floor.pid, []).append(asset.local_path)
                elif asset.exportable:
                    archived_images.setdefault(floor.pid, []).append(asset.local_path)
                else:
                    non_export_images.setdefault(floor.pid, []).append(asset.local_path)
            elif _classify_backfill_image(url) == "embedded":
                skipped_image_urls.setdefault(floor.pid, []).append(url)
    return {
        "archived_images": archived_images,
        "non_export_images": non_export_images,
        "shared_images": shared_images,
        "skipped_image_urls": skipped_image_urls,
    }


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _classify_backfill_image(url: str) -> str:
    lower = url.lower().strip()
    parsed = urlparse(lower)
    if lower.startswith("data:") or lower.startswith("http://data:") or lower.startswith("https://data:"):
        return "embedded"
    if "/static/image/" in lower:
        if parsed.path.endswith("/common/back.gif"):
            return "decorative"
        return "static"
    return "content"


def _local_availability(
    *,
    tid: int,
    url: str,
    image_class: str,
    asset: _LocalAsset | None,
    paths: StoragePaths,
) -> str:
    if image_class in {"static", "decorative"} and paths.shared_asset_path(url).exists():
        return "ok"
    if asset is None:
        return "not_in_assets"
    if asset.local_path:
        path = _asset_path(tid=tid, local_path=asset.local_path, paths=paths)
        if path.exists():
            return "ok"
        return "file_missing"
    if asset.status in {"missing", "pending"}:
        return str(asset.status)
    return "no_local_path"


def _asset_path(*, tid: int, local_path: str, paths: StoragePaths) -> Path:
    path = Path(local_path)
    if path.is_absolute():
        return path
    if str(path).startswith("shared/"):
        return paths.data_dir / path
    return paths.thread_dir(tid) / path


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _archive_generation(thread, *, fixed_after: str | None) -> dict[str, Any]:
    sync_time = thread["sync_time"] if "sync_time" in thread.keys() else None
    evidence = "unknown"
    if fixed_after and sync_time:
        evidence = "post_fix" if str(sync_time) >= str(fixed_after) else "pre_fix"
    return {
        "sync_time": _iso(sync_time),
        "fixed_after": fixed_after,
        "policy_state": evidence,
    }
