from __future__ import annotations

import json
import logging
import shutil
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
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
from yamibo_mcp.storage.images import _is_valid_image_file
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.daemon.handlers.update_thread import _load_local_thread_snapshot
from yamibo_mcp.daemon.handlers.sync_thread import _check_cancelled, _check_paused
from yamibo_mcp.errors import ThreadPermissionRequiredError, RemoteMaintenanceError, UnexpectedPageError, _extract_permission_code
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, next_permission_threshold
from yamibo_mcp.yamibo.anti_bot import ensure_no_maintenance_pause
from yamibo_mcp.yamibo.parsers.thread_detail import parse_thread_snapshot
from yamibo_mcp.yamibo.proxy_pool import activate_proxy_binding, select_thread_proxy
from yamibo_mcp.yamibo.urls import stable_attachment_id


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
    scope = str(job.payload.get("scope") or "non_first_floor").strip().lower()
    selected_scope = scope == "selected"
    include_first_floor = bool(job.payload.get("include_first_floor", selected_scope))
    target_urls = _unique([str(url) for url in (job.payload.get("target_urls") or []) if str(url).strip()])

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
    target_positions = _target_positions(paths, tid, job.payload, target_urls)
    expected_paths = _expected_target_paths(paths, tid, target_positions, target_urls)
    if selected_scope and target_urls:
        reconciled = _reconcile_selected_targets(
            repo.conn,
            paths=paths,
            tid=tid,
            target_urls=target_urls,
            target_positions=target_positions,
            expected_paths=expected_paths,
            target_asset_id=str(job.payload.get("target_asset_id") or ""),
        )
        if reconciled == set(target_urls):
            repo.update_stage(job.job_id, "reconcile", progress_current=4, progress_total=4)
            repo.succeed(
                job.job_id,
                {
                    "tid": tid,
                    "scope": "selected",
                    "target_urls": target_urls,
                    "reconciled_urls": sorted(reconciled),
                    "downloaded_image_count": 0,
                    "reconciled_image_count": len(reconciled),
                },
            )
            return
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
                selected_url_map = (
                    _resolve_selected_remote_urls(snapshot, target_urls, target_positions)
                    if selected_scope and target_urls
                    else {}
                )
                selected_remote_urls = set(selected_url_map.values())
                if selected_scope and target_urls and not selected_remote_urls:
                    repo.fail(
                        job.job_id,
                        "IMAGE_TARGET_NOT_FOUND",
                        "selected image target was not found at its recorded position or stable attachment id",
                        {"tid": tid, "scope": "selected", "target_urls": target_urls},
                    )
                    return
                diff = _diff_snapshot_images(
                    snapshot,
                    local_assets=local_assets,
                    paths=paths,
                    include_shared_static=True,
                    scope=scope,
                    include_first_floor=include_first_floor,
                    target_urls=(
                        selected_remote_urls
                        if selected_scope and target_urls
                        else (set(target_urls) if target_urls else None)
                    ),
                )

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
                    retries=_selected_download_retries(
                        target_urls=set(target_urls) if selected_scope else None,
                        configured_retries=settings.image_download_retries,
                    ),
                    headers=client.headers,
                    cookie_jar=client.cookie_jar,
                    cookie_file=getattr(client, "cookie_file", None),
                    use_system_proxy=client.use_system_proxy,
                    proxy_url=getattr(client, "proxy_url", None),
                    referer=final_url,
                    target_urls={str(item["url"]) for item in diff["missing_items_for_apply"]},
                    stage_deadline_seconds=float(getattr(settings, "image_download_stage_timeout_seconds", 600.0)),
                )
                local_path_by_url = _build_downloaded_url_map(missing_snapshot, image_result)
                content = build_content_snapshot(apply_snapshot, forum_id=thread["forum_id"] if "forum_id" in thread.keys() else None)
                synced_assets = _merge_assets(
                    content.assets,
                    local_assets=local_assets,
                    local_path_by_url=local_path_by_url,
                    image_result=image_result,
                    protected_urls=selected_remote_urls if selected_scope else set(),
                    expected_paths=expected_paths,
                )
                image_slot_overrides = _image_slot_overrides_from_assets(synced_assets)
                archive_maps = _archive_maps_from_assets(apply_snapshot, synced_assets)
                previous_missing = _thread_missing_urls(thread)
                previous_missing_shared = _archive_missing_shared_urls(paths, tid)
                successful_urls = set(image_result.relative_path_by_url)
                if selected_scope:
                    successful_urls = _successful_selected_url_aliases(successful_urls, selected_url_map)
                    missing_image_urls, missing_shared_image_urls = _missing_urls_from_assets(synced_assets)
                else:
                    missing_image_urls = _unique([
                        *[url for url in previous_missing if url not in successful_urls],
                        *image_result.missing_urls,
                    ])
                    missing_shared_image_urls = _unique([
                        *[url for url in previous_missing_shared if url not in successful_urls],
                        *image_result.missing_shared_urls,
                    ])
                archive_status = "partial" if (missing_image_urls or missing_shared_image_urls or image_result.stopped_reason) else "complete"
                resolved_selected_urls = {
                    asset.remote_url
                    for asset in synced_assets
                    if asset.remote_url in (selected_remote_urls or set(target_urls)) and asset.local_path
                }

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
                    if selected_scope:
                        _update_selected_assets(
                            repo.conn,
                            tid=apply_snapshot.tid,
                            remote_assets=synced_assets,
                            local_assets=local_assets,
                            local_path_by_url=local_path_by_url,
                        )
                    else:
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
                    image_slot_overrides=image_slot_overrides,
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
                    image_slot_overrides=image_slot_overrides,
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
                if selected_scope and not resolved_selected_urls:
                    repo.fail(
                        job.job_id,
                        "IMAGE_TARGET_NOT_DOWNLOADED",
                        "selected image target was not downloaded or reconciled",
                        artifacts,
                    )
                elif selected_scope and set(selected_remote_urls or target_urls).issubset(resolved_selected_urls):
                    repo.succeed(job.job_id, artifacts)
                elif archive_status == "partial":
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


def _load_archive_metadata(paths: StoragePaths, tid: int) -> dict[str, Any]:
    path = paths.thread_metadata(tid)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, TypeError):
        return {}


def _target_positions(paths: StoragePaths, tid: int, payload: dict[str, Any], target_urls: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve target identity from payload, falling back to archive metadata.

    ``assets.local_path`` is deliberately not consulted here.  The metadata's
    ``remote_image_urls`` preserves the source slot even when an older archive
    compressed successful paths and assigned them to the wrong URLs.
    """
    positions: dict[str, dict[str, Any]] = {}
    raw_positions = payload.get("target_positions") or []
    if isinstance(raw_positions, dict):
        raw_positions = [raw_positions]
    for raw in raw_positions:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or raw.get("remote_url") or "")
        if not url:
            continue
        try:
            positions[url] = {
                "pid": int(raw["pid"]) if raw.get("pid") is not None else None,
                "floor_no": int(raw["floor_no"]) if raw.get("floor_no") is not None else None,
                "image_index": int(raw.get("image_index") or raw.get("index") or 0),
            }
        except (TypeError, ValueError):
            continue
    if all(url in positions for url in target_urls):
        return positions
    metadata = _load_archive_metadata(paths, tid)
    for floor in metadata.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        urls = list(floor.get("remote_image_urls") or floor.get("image_urls") or [])
        for index, url in enumerate(urls, start=1):
            url = str(url)
            if url not in target_urls or url in positions:
                continue
            positions[url] = {
                "pid": floor.get("pid"),
                "floor_no": floor.get("floor_no"),
                "image_index": index,
            }
    return positions


def _suffix_for_target(url: str, metadata: dict[str, Any] | None = None) -> str:
    suffix = Path(urlparse(url).path).suffix
    if suffix and len(suffix) <= 8 and suffix.lower() != ".php":
        return suffix
    # Attachment/image URLs commonly have no extension.  Existing metadata
    # may still carry the authoritative materialized suffix for reconciliation.
    if metadata:
        candidate = Path(str(metadata.get("local_path") or "")).suffix
        if candidate and len(candidate) <= 8:
            return candidate
    return ".jpg"


def _expected_target_paths(
    paths: StoragePaths,
    tid: int,
    positions: dict[str, dict[str, Any]],
    target_urls: list[str],
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for url in target_urls:
        position = positions.get(url) or {}
        try:
            floor_no = int(position.get("floor_no"))
            image_index = int(position.get("image_index"))
        except (TypeError, ValueError):
            continue
        if floor_no <= 0 or image_index <= 0:
            continue
        stem = f"floor_{floor_no:03d}_{image_index:02d}"
        image_dir = paths.thread_images_dir(tid)
        existing = next((candidate for candidate in sorted(image_dir.glob(f"{stem}.*")) if _is_valid_image_file(candidate)), None)
        result[url] = existing or image_dir / f"{stem}{_suffix_for_target(url)}"
    return result


def _reconcile_selected_targets(
    conn,
    *,
    paths: StoragePaths,
    tid: int,
    target_urls: list[str],
    target_positions: dict[str, dict[str, Any]],
    expected_paths: dict[str, Path],
    target_asset_id: str,
) -> set[str]:
    """Repair DB/metadata from an already-valid expected file without HTTP."""
    if not expected_paths:
        return set()
    asset_rows = AssetsRepository(conn).list_assets(tid)
    asset_by_url = {str(row["remote_url"]): row for row in asset_rows if row["remote_url"]}
    metadata = _load_archive_metadata(paths, tid)
    reconciled: set[str] = set()
    for url in target_urls:
        path = expected_paths.get(url)
        if path is None or not _is_valid_image_file(path):
            continue
        row = asset_by_url.get(url)
        if row is None and target_asset_id:
            row = next((candidate for candidate in asset_rows if str(candidate["asset_id"]) == target_asset_id), None)
        if row is not None and target_asset_id and str(row["asset_id"]) != target_asset_id:
            row = None
        if row is None:
            continue
        relative_path = str(path.relative_to(paths.thread_dir(tid)))
        conn.execute(
            "UPDATE assets SET local_path = ?, status = 'downloaded' WHERE tid = ? AND asset_id = ? AND remote_url = ?",
            (relative_path, tid, row["asset_id"], url),
        )
        reconciled.add(url)

    if not reconciled:
        return reconciled
    missing = _thread_missing_urls_from_metadata(metadata)
    missing = [url for url in missing if url not in reconciled]
    metadata_changed = False
    for floor in metadata.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        slots = floor.get("image_slots") or []
        floor_changed = False
        for slot in slots:
            if not isinstance(slot, dict) or str(slot.get("remote_url") or "") not in reconciled:
                continue
            url = str(slot["remote_url"])
            slot["local_path"] = str(expected_paths[url].relative_to(paths.thread_dir(tid)))
            slot["status"] = "shared" if _classify_backfill_image(url) in {"static", "decorative"} else "content"
            floor.setdefault("content_image_urls", [])
            if slot["status"] == "content" and slot["local_path"] not in floor["content_image_urls"]:
                floor["content_image_urls"].append(slot["local_path"])
            floor.setdefault("image_urls", [])
            if slot["status"] == "content" and slot["local_path"] not in floor["image_urls"]:
                floor["image_urls"].append(slot["local_path"])
            floor["missing_image_urls"] = [value for value in floor.get("missing_image_urls") or [] if value != url]
            metadata_changed = True
            floor_changed = True
        if floor_changed:
            # Rebuild the materialized-path arrays from the original remote
            # URL order.  Appending a reconciled path would shift every later
            # slot (the exact corruption this endpoint repairs).
            slot_by_url = {
                str(slot.get("remote_url")): slot
                for slot in slots
                if isinstance(slot, dict) and slot.get("remote_url")
            }
            ordered_urls = list(floor.get("remote_image_urls") or [])
            ordered_content = [
                str(slot_by_url[url].get("local_path"))
                for url in ordered_urls
                if url in slot_by_url and slot_by_url[url].get("local_path")
                and slot_by_url[url].get("status") == "content"
            ]
            ordered_non_export = [
                str(slot_by_url[url].get("local_path"))
                for url in ordered_urls
                if url in slot_by_url and slot_by_url[url].get("local_path")
                and slot_by_url[url].get("status") == "non_export"
            ]
            ordered_shared = [
                str(slot_by_url[url].get("local_path"))
                for url in ordered_urls
                if url in slot_by_url and slot_by_url[url].get("local_path")
                and slot_by_url[url].get("status") == "shared"
            ]
            floor["content_image_urls"] = ordered_content
            floor["non_export_image_urls"] = ordered_non_export
            floor["shared_image_urls"] = ordered_shared
            floor["image_urls"] = ordered_content + ordered_non_export
    metadata["missing_image_urls"] = missing
    metadata["archive_status"] = "partial" if missing or metadata.get("missing_shared_image_urls") else "complete"
    if metadata_changed:
        atomic_write_text(paths.thread_metadata(tid), json.dumps(metadata, ensure_ascii=False, indent=2))
    conn.execute(
        "UPDATE threads SET missing_images_json = ?, archive_status = ? WHERE tid = ?",
        (json.dumps(missing, ensure_ascii=False), metadata["archive_status"], tid),
    )
    conn.commit()
    return reconciled


def _thread_missing_urls_from_metadata(metadata: dict[str, Any]) -> list[str]:
    values = metadata.get("missing_image_urls") or []
    return [str(value) for value in values if str(value).strip()]


def _thread_missing_urls(thread) -> list[str]:
    try:
        value = thread["missing_images_json"] if "missing_images_json" in thread.keys() else "[]"
        parsed = json.loads(value or "[]")
        return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _archive_missing_shared_urls(paths: StoragePaths, tid: int) -> list[str]:
    metadata = _load_archive_metadata(paths, tid)
    values = metadata.get("missing_shared_image_urls") or []
    return [str(value) for value in values if str(value).strip()]


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
    scope: str = "non_first_floor",
    include_first_floor: bool = False,
    target_urls: set[str] | None = None,
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
            if target_urls is not None and url not in target_urls:
                continue
            if not include_first_floor and floor.floor_no <= 1:
                continue
            remote_non_first_image_count += 1
            image_class = _classify_backfill_image(url)
            # Automatic idle repair intentionally remains restricted to known
            # forum/static assets.  A user-selected external URL may still be
            # attempted through the interactive endpoint.
            if image_class == "external" and scope != "selected":
                continue
            class_counts[image_class] += 1
            availability = _local_availability(
                tid=snapshot.tid,
                url=url,
                image_class=image_class,
                asset=_local_asset_for_url(local_assets, url),
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
            if image_class in {"content", "external"}:
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
    pids: set[int] = set()
    for item in missing_items:
        pids.add(int(item["pid"]))
    floors = [
        # Keep every original URL on the selected floor.  The downloader's
        # target_urls filter decides what to fetch, while enumerate() retains
        # the source index for a stable floor_XXX_YY filename.
        floor
        for floor in snapshot.floors
        if floor.pid in pids
    ]
    return replace(snapshot, floors=floors, image_count=sum(len(floor.image_urls) for floor in floors))


def _build_downloaded_url_map(snapshot, image_result) -> dict[str, str]:
    local_path_by_url: dict[str, str] = dict(getattr(image_result, "relative_path_by_url", {}) or {})
    for floor in snapshot.floors:
        if any(url in local_path_by_url for url in floor.image_urls):
            continue
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


def _merge_assets(
    remote_assets,
    *,
    local_assets: dict[str, _LocalAsset],
    local_path_by_url: dict[str, str],
    image_result,
    protected_urls: set[str] | None = None,
    expected_paths: dict[str, Path] | None = None,
):
    merged = []
    missing = set(image_result.missing_urls) | set(image_result.missing_shared_urls)
    protected_urls = protected_urls or set()
    for asset in remote_assets:
        existing = _local_asset_for_url(local_assets, asset.remote_url)
        if asset.remote_url in local_path_by_url:
            local_path = local_path_by_url[asset.remote_url]
        elif asset.remote_url in protected_urls:
            expected = expected_paths.get(asset.remote_url) if expected_paths else None
            local_path = (
                str(expected.relative_to(expected.parents[1]))
                if expected is not None and _is_valid_image_file(expected)
                else None
            )
        else:
            local_path = existing.local_path if existing and existing.local_path else None
        status = "downloaded" if local_path else ("missing" if asset.remote_url in missing else (existing.status if existing and existing.status else asset.status))
        merged.append(replace(asset, local_path=local_path, status=status))
    return merged


def _local_asset_for_url(local_assets: dict[str, _LocalAsset], url: str) -> _LocalAsset | None:
    existing = local_assets.get(url)
    if existing is not None:
        return existing
    stable_id = _stable_attachment_id(url)
    if stable_id is None:
        return None
    return next(
        (candidate for candidate in local_assets.values() if _stable_attachment_id(candidate.remote_url) == stable_id),
        None,
    )


def _resolve_selected_remote_urls(
    snapshot,
    target_urls: list[str],
    target_positions: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Map submitted URLs to the current remote URLs after a signature rotation.

    Position is the primary identity.  For Yamibo attachments, the decoded aid
    is then checked as a stable identity so a reused slot cannot download the
    wrong attachment.  External URLs retain exact URL matching.
    """
    result: dict[str, str] = {}
    by_position = {
        (floor.pid, index): url
        for floor in snapshot.floors
        for index, url in enumerate(floor.image_urls, start=1)
    }
    for submitted in target_urls:
        position = target_positions.get(submitted) or {}
        pid = position.get("pid")
        image_index = position.get("image_index")
        candidate = by_position.get((int(pid), int(image_index))) if pid is not None and image_index else None
        if candidate is not None:
            submitted_aid = _stable_attachment_id(submitted)
            candidate_aid = _stable_attachment_id(candidate)
            if (submitted_aid is not None and submitted_aid == candidate_aid) or (
                submitted_aid is None and candidate == submitted
            ):
                result[submitted] = candidate
                continue
        if any(url == submitted for floor in snapshot.floors for url in floor.image_urls):
            result[submitted] = submitted
    return result


def _successful_selected_url_aliases(
    successful_urls: set[str],
    selected_url_map: dict[str, str],
) -> set[str]:
    """Include stale submitted signatures when their current URL succeeded."""
    return successful_urls | {
        submitted_url
        for submitted_url, current_url in selected_url_map.items()
        if current_url in successful_urls
    }


def _stable_attachment_id(url: str) -> str | None:
    """Compatibility wrapper for existing tests and internal call sites."""
    return stable_attachment_id(url)


def _update_selected_assets(
    conn,
    *,
    tid: int,
    remote_assets,
    local_assets: dict[str, _LocalAsset],
    local_path_by_url: dict[str, str],
) -> None:
    """Update only selected rows, retaining every other asset untouched."""
    downloaded = set(local_path_by_url)
    rows = AssetsRepository(conn).list_assets(tid)
    for asset in remote_assets:
        if asset.remote_url not in downloaded:
            continue
        old = _local_asset_for_url(local_assets, asset.remote_url)
        if old is None:
            continue
        row = next((candidate for candidate in rows if str(candidate["remote_url"]) == old.remote_url), None)
        if row is None:
            continue
        local_path = local_path_by_url.get(asset.remote_url)
        if not local_path:
            continue
        conn.execute(
            "UPDATE assets SET pid = ?, remote_url = ?, local_path = ?, status = 'downloaded' WHERE tid = ? AND asset_id = ?",
            (asset.pid, asset.remote_url, local_path, tid, row["asset_id"]),
        )


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


def _image_slot_overrides_from_assets(assets) -> dict[str, dict[str, str | None]]:
    overrides: dict[str, dict[str, str | None]] = {}
    for asset in assets:
        image_class = _classify_backfill_image(asset.remote_url)
        if asset.local_path:
            if image_class in {"static", "decorative"}:
                status = "shared"
            elif asset.exportable:
                status = "content"
            else:
                status = "non_export"
        elif image_class == "embedded":
            status = "skipped"
        else:
            status = "missing"
        overrides[asset.remote_url] = {"local_path": asset.local_path, "status": status}
    return overrides


def _missing_urls_from_assets(assets) -> tuple[list[str], list[str]]:
    missing_images: list[str] = []
    missing_shared: list[str] = []
    for asset in assets:
        if asset.local_path or _classify_backfill_image(asset.remote_url) == "embedded":
            continue
        if _classify_backfill_image(asset.remote_url) in {"static", "decorative"}:
            missing_shared.append(asset.remote_url)
        else:
            missing_images.append(asset.remote_url)
    return _unique(missing_images), _unique(missing_shared)


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
    if _is_site_attachment(url) or "/data/attachment/" in parsed.path.lower():
        return "content"
    if parsed.scheme in {"http", "https"}:
        return "external"
    return "content"


def _selected_download_retries(*, target_urls: set[str] | None, configured_retries: int) -> int:
    """Avoid spending an interactive retry budget on dead third-party links."""
    if target_urls and any(_classify_backfill_image(url) == "external" for url in target_urls):
        return 0
    return max(0, int(configured_retries))


def _is_site_attachment(url: str) -> bool:
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "bbs.yamibo.com" or parsed.path.lower() != "/forum.php":
        return False
    query = parse_qs(parsed.query)
    mod_values = {value.lower() for value in query.get("mod", [])}
    return "attachment" in mod_values or "attachment/image" in mod_values


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
        if _is_valid_image_file(path):
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
