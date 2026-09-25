from __future__ import annotations

import json
import logging
import shutil
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.content import build_content_snapshot
from yamibo_mcp.domain.models import FloorSnapshot, Job, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.domain.validation import validate_floor_sequence
from yamibo_mcp.storage.images import download_images_to_staging
from yamibo_mcp.storage.images import _is_valid_image_file
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.daemon.handlers.update_thread import _load_local_thread_snapshot
from yamibo_mcp.daemon.handlers.sync_thread import (
    _build_download_control_checks,
    _check_cancelled,
    _check_paused,
    _merge_thread_snapshots,
)
from yamibo_mcp.daemon.heartbeat import HeartbeatPacer
from yamibo_mcp.errors import (
    LeaseNotAcquired,
    ThreadPermissionRequiredError,
    UnexpectedPageError,
    _extract_permission_code,
    is_direct_transport_fallback_error,
)
from yamibo_mcp.structured_logging import emit
from yamibo_mcp.yamibo.account_pool import borrow_yamibo_client, next_permission_threshold
from yamibo_mcp.yamibo.anti_bot import ensure_no_maintenance_pause
from yamibo_mcp.yamibo.parsers.thread_detail import parse_thread_snapshot
from yamibo_mcp.yamibo.proxy_pool import DIRECT_NODE_NAME, activate_proxy_binding, select_thread_proxy
from yamibo_mcp.daemon.remote_attempt import build_attempt
from yamibo_mcp.yamibo.urls import is_yamibo_site_content_image_url as _is_yamibo_site_content_url
from yamibo_mcp.yamibo.urls import is_yamibo_site_image_url as _is_yamibo_site_url
from yamibo_mcp.yamibo.urls import remote_image_identity, stable_attachment_id, thread_url_from_tid


LOG = logging.getLogger(__name__)

_FOREGROUND_WORK_STATUSES = (
    "queued",
    "running",
    "retrying",
    "interrupted",
    "paused",
)


def _foreground_work_available(repo: JobsRepository, job_id: str) -> bool:
    # A foreground export can hand off its own image repair as a child job.
    # The parent remains retrying while this child runs, but that dependency
    # must not make the child yield to the foreground job that is waiting for
    # it.  Other unrelated foreground jobs still retain priority.
    ignored_job_ids = {job_id}
    cursor = job_id
    for _ in range(8):
        parent_row = repo.conn.execute(
            "SELECT parent_job_id FROM jobs WHERE job_id = ?",
            (cursor,),
        ).fetchone()
        parent_job_id = parent_row["parent_job_id"] if parent_row is not None else None
        if not parent_job_id or str(parent_job_id) in ignored_job_ids:
            break
        parent_job_id = str(parent_job_id)
        ignored_job_ids.add(parent_job_id)
        cursor = parent_job_id

    placeholders = ",".join("?" for _ in _FOREGROUND_WORK_STATUSES)
    rows = repo.conn.execute(
        f"""
        SELECT job_id, job_type, payload_json FROM jobs
        WHERE status IN ({placeholders})
        """,
        _FOREGROUND_WORK_STATUSES,
    ).fetchall()
    for row in rows:
        if str(row["job_id"]) in ignored_job_ids:
            continue
        payload = row["payload_json"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        if row["job_type"] != "image_backfill" or not bool((payload or {}).get("internal_auto")):
            return True
    return False


def _safe_image_diagnostic(item: dict[str, Any]) -> dict[str, Any]:
    """Keep job events useful without persisting cookies or query-bearing URLs."""
    result = {key: value for key, value in item.items() if key not in {"url", "final_url"}}
    for key in ("url", "final_url"):
        value = item.get(key)
        if value:
            parsed = urlparse(str(value))
            result[f"{key}_host"] = parsed.netloc
            result[f"{key}_path"] = parsed.path
            attachment_id = stable_attachment_id(str(value))
            if attachment_id is not None:
                result["remote_identity"] = attachment_id
    return result


@dataclass(frozen=True)
class _LocalAsset:
    remote_url: str
    asset_type: str
    local_path: str | None
    status: str | None


def _should_retry_image_download(image_result, diagnostics: list[dict[str, Any]]) -> bool:
    if image_result.stopped_reason in {"stage_timeout", "download_slot_timeout"}:
        return True
    return any(
        item.get("status") != "ok"
        and (bool(item.get("retryable")) or item.get("error_type") == "waf_response")
        for item in diagnostics
    )


def _image_retry_delay_seconds(diagnostics: list[dict[str, Any]], *, retry_count: int) -> int | None:
    throttled = [item for item in diagnostics if item.get("http_status") in {429, 503}]
    if not throttled:
        return None
    delay = min(60 * (2 ** min(max(retry_count, 0), 6)), 3600)
    for item in throttled:
        headers = item.get("response_headers") or {}
        if not isinstance(headers, dict):
            continue
        value = headers.get("Retry-After")
        if value is None:
            continue
        try:
            suggested = int(str(value).strip())
        except ValueError:
            try:
                suggested = int((parsedate_to_datetime(str(value)) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                continue
        delay = max(delay, suggested)
    return min(max(delay, 1), 3600)


def _image_download_failure_code(diagnostics: list[dict[str, Any]]) -> str:
    """Map per-image diagnostics to the task-level failure classification."""
    if any(
        item.get("status") != "ok" and item.get("error_type") == "waf_response"
        for item in diagnostics
    ):
        # The remote service returned an interception page.  This is not
        # evidence that the selected attachment is gone, even when the edge
        # used HTTP 404/403 for the block.
        return "REMOTE_SOFT_BLOCK"
    return "IMAGE_TARGET_NOT_DOWNLOADED"


def _needs_attachment_url_refresh(failed_urls: set[str], diagnostics: list[dict[str, Any]]) -> bool:
    return any(
        _is_site_attachment(url)
        and any(
            item.get("url") == url
            and item.get("error_type") != "waf_response"
            and (
                item.get("http_status") in {400, 403, 404, 410}
                or item.get("error_type") in {"html_response", "invalid_image_body"}
            )
            for item in diagnostics
        )
        for url in failed_urls
    )


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

    upgrade_to_full = bool(job.payload.get("upgrade_to_full"))
    dry_run = False if upgrade_to_full else bool(job.payload.get("dry_run", True))
    scope = "selected" if upgrade_to_full else str(job.payload.get("scope") or "non_first_floor").strip().lower()
    reconcile_mode = str(job.payload.get("mode") or "").strip().lower() == "reconcile_missing"
    selected_scope = scope == "selected" or reconcile_mode
    include_first_floor = True if upgrade_to_full else bool(job.payload.get("include_first_floor", selected_scope))
    target_urls = _unique([str(url) for url in (job.payload.get("target_urls") or []) if str(url).strip()])
    site_only = bool(job.payload.get("internal_auto"))
    if site_only:
        target_urls = [url for url in target_urls if _is_yamibo_site_url(url)]

    max_pages = max(int(job.payload.get("max_pages") or getattr(settings, "image_backfill_max_pages", 1)), 1)
    base_url = str(job.payload.get("base_url") or "https://bbs.yamibo.com")
    fixed_after = job.payload.get("fixed_after") or getattr(settings, "image_backfill_fixed_after", None)
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir, novel_txt_export_dir=settings.novel_txt_export_dir)

    repo.update_stage(job.job_id, "load_local", progress_current=1, progress_total=4)
    thread = ThreadsRepository(repo.conn).get_thread(tid)
    if thread is None:
        raise ValueError(f"local archive not found for image_backfill: {tid}")
    if thread["capture_mode"] == "text_only" and not upgrade_to_full:
        raise ValueError("text-only archive requires an explicit full upgrade before image backfill")
    asset_rows = AssetsRepository(repo.conn).list_assets(tid)
    local_assets = _local_assets_by_url(asset_rows)
    local_snapshot = _load_local_snapshot_for_backfill(paths, repo.conn, thread)
    if upgrade_to_full:
        if thread["capture_mode"] == "text_only" and thread["archive_status"] != "complete":
            raise ValueError("full upgrade requires a complete text-only archive")
        if thread["capture_mode"] not in {"text_only", "full"} or thread["archive_status"] not in {"complete", "partial"}:
            raise ValueError("full upgrade requires an archived thread")
        upgrade_diff = _diff_snapshot_images(
            local_snapshot,
            local_assets=local_assets,
            paths=paths,
            scope="selected",
            include_first_floor=True,
            site_only=False,
        )
        target_urls = _unique([str(item["url"]) for item in upgrade_diff["missing_items_for_apply"]])
        if not target_urls:
            with transaction(repo.conn):
                repo.assert_lease(job.job_id, for_update=True)
                repo.conn.execute(
                    "UPDATE threads SET capture_mode = 'full', archive_status = 'complete', missing_images_json = '[]' WHERE tid = ?",
                    (tid,),
                )
            repo.succeed(job.job_id, {
                "tid": tid, "capture_mode": "full", "archive_status_after": "complete",
                "already_complete": True, "downloaded_image_count": 0,
            })
            return
    target_positions = _target_positions(paths, tid, job.payload, target_urls)
    if reconcile_mode and not dry_run and not target_urls:
        target_urls = _metadata_missing_targets(paths, tid, site_only=site_only)
        metadata_positions = _metadata_target_positions(paths, tid)
        target_positions = {url: metadata_positions[url] for url in target_urls if url in metadata_positions}
    expected_paths = _expected_target_paths(paths, tid, target_positions, target_urls)
    if reconcile_mode and not dry_run and not target_urls:
        image_rows = [row for row in asset_rows if row["asset_type"] in {"image", "attachment"}]
        covered_pids = {row["pid"] for row in image_rows}
        # Empty missing metadata alone is not proof: check coverage and files.
        if (
            image_rows
            and len(image_rows) >= int(thread["image_count"] or 0)
            and all(not floor.has_images or floor.pid in covered_pids for floor in local_snapshot.floors)
            and not _diff_snapshot_images(
                local_snapshot, local_assets=local_assets, paths=paths,
                scope="selected", include_first_floor=True,
                site_only=site_only,
            )["missing_items_for_apply"]
            and all(
                row["local_path"] and _is_valid_image_file(
                    _asset_path(tid=tid, local_path=row["local_path"], paths=paths)
                )
                for row in image_rows
            )
        ):
            repo.update_stage(job.job_id, "reconcile", progress_current=4, progress_total=4)
            repo.succeed(job.job_id, {
                "tid": tid, "mode": "reconcile_missing",
                "campaign": job.payload.get("campaign"),
                "already_complete": True, "downloaded_image_count": 0,
                "remote_fetch": False,
            })
            return
    if reconcile_mode and not dry_run and target_urls:
        reconciled = _reconcile_missing_targets(
            repo, job_id=job.job_id, paths=paths, tid=tid, target_urls=target_urls,
        )
        target_urls = [url for url in target_urls if url not in reconciled]
        if not target_urls:
            repo.update_stage(job.job_id, "reconcile", progress_current=4, progress_total=4)
            repo.succeed(job.job_id, {
                "tid": tid,
                "mode": "reconcile_missing",
                "campaign": job.payload.get("campaign"),
                "reconciled_image_count": len(reconciled),
                "downloaded_image_count": 0,
                "remote_fetch": False,
            })
            return
    strict_selected_scope = selected_scope and bool(target_urls)
    if strict_selected_scope:
        reconciled = _reconcile_selected_targets(
            repo,
            job_id=job.job_id,
            paths=paths,
            tid=tid,
            target_urls=target_urls,
            target_positions=target_positions,
            expected_paths=expected_paths,
            target_asset_id=str(job.payload.get("target_asset_id") or ""),
        )
        if reconciled == set(target_urls):
            repo.update_stage(job.job_id, "reconcile", progress_current=4, progress_total=4)
            if upgrade_to_full:
                with transaction(repo.conn):
                    repo.assert_lease(job.job_id, for_update=True)
                    repo.conn.execute(
                        "UPDATE threads SET capture_mode = 'full', archive_status = 'complete', missing_images_json = '[]' WHERE tid = ?",
                        (tid,),
                    )
            repo.succeed(
                job.job_id,
                {
                    "tid": tid,
                    "scope": "selected",
                    "capture_mode": "full" if upgrade_to_full else thread["capture_mode"],
                    "target_urls": target_urls,
                    "reconciled_urls": sorted(reconciled),
                    "downloaded_image_count": 0,
                    "reconciled_image_count": len(reconciled),
                },
            )
            return
    if not dry_run:
        floor_sequence_errors = validate_floor_sequence(local_snapshot.floors)
        if floor_sequence_errors:
            raise ValueError(
                "local floor sequence is invalid; full resync required: "
                + "; ".join(floor_sequence_errors)
            )
    archive_generation = _archive_generation(thread, fixed_after=fixed_after)
    direct_first = _prefers_direct_transport(job)
    direct = direct_first
    proxy_binding = None
    proxy_pool_artifacts: dict[str, object] = {}
    if not direct:
        proxy_binding, proxy_pool_artifacts = _select_backfill_proxy(
            settings,
            repo,
            tid=tid,
            job_id=job.job_id,
        )
    record_attempt = getattr(repo, "record_remote_attempt", None)
    if callable(record_attempt):
        record_attempt(job.job_id, build_attempt(
            source="image_thread_detail",
            node=DIRECT_NODE_NAME if direct else getattr(proxy_binding, "node", None),
            retry_hint=(proxy_binding.diagnostics or {}).get("retry_hint") if proxy_binding else None,
            candidate_tier=(proxy_binding.diagnostics or {}).get("candidate_tier") if proxy_binding else None,
            transport="direct" if direct else "proxy",
        ))
    proxy_url = proxy_binding.proxy_url if proxy_binding else None
    direct_fallback_error: str | None = None
    used_account_ids = _attempted_account_ids(repo, job.job_id)

    def _switch_to_proxy_after_direct_failure(exc: BaseException) -> bool:
        nonlocal direct, direct_fallback_error, proxy_binding, proxy_pool_artifacts, proxy_url
        if not direct or not is_direct_transport_fallback_error(exc):
            return False
        next_binding, next_artifacts = _select_backfill_proxy(
            settings,
            repo,
            tid=tid,
            job_id=job.job_id,
            exclude_nodes={DIRECT_NODE_NAME},
        )
        if next_binding is None:
            return False
        direct = False
        direct_fallback_error = str(exc)
        proxy_binding = next_binding
        proxy_pool_artifacts = next_artifacts
        proxy_url = proxy_binding.proxy_url
        if callable(record_attempt):
            record_attempt(job.job_id, build_attempt(
                source="image_thread_detail",
                node=proxy_binding.node,
                retry_hint=(proxy_binding.diagnostics or {}).get("retry_hint"),
                candidate_tier=(proxy_binding.diagnostics or {}).get("candidate_tier"),
                transport="proxy",
                direct_fallback_error=direct_fallback_error,
            ))
        LOG.info(
            "image_backfill job=%s tid=%s retrying through proxy after direct transport failure node=%s",
            job.job_id,
            tid,
            proxy_binding.node,
        )
        return True

    if reconcile_mode and _foreground_work_available(repo, job.job_id):
        repo.retry_later(
            job.job_id,
            error_code="FOREGROUND_PRIORITY",
            error_message="foreground work is queued; yielding maintenance job",
            delay_seconds=1,
        )
        return
    repo.update_stage(job.job_id, "fetch_remote", progress_current=2, progress_total=4)
    fetch_lease_seconds = max(
        lease_seconds,
        int(max(float(getattr(settings, "request_timeout_seconds", 30.0)) * 4.0, 120.0)),
    )
    fetch_heartbeat = HeartbeatPacer(
        repo=repo,
        job_id=job.job_id,
        worker_id=worker_id,
        lease_seconds=fetch_lease_seconds,
        min_interval_seconds=max(float(getattr(settings, "worker_heartbeat_seconds", 15)), 1.0),
    )
    fetch_heartbeat.beat(force=True)
    min_permission: int | None = None
    while True:
        identity = None
        borrowed_client = False
        try:
            _check_cancelled(repo, job.job_id)
            _check_paused(repo, job.job_id)
            fetch_heartbeat.beat()
            if reconcile_mode and _foreground_work_available(repo, job.job_id):
                repo.retry_later(
                    job.job_id,
                    error_code="FOREGROUND_PRIORITY",
                    error_message="foreground work appeared; yielding maintenance job",
                    delay_seconds=1,
                )
                return
            ensure_no_maintenance_pause(repo.conn)

            with ExitStack() as stack:
                stack.enter_context(activate_proxy_binding(settings, proxy_binding))
                borrow_kwargs: dict[str, Any] = {
                    "min_permission": min_permission,
                    "proxy_url": proxy_url,
                    "force_direct": direct,
                }
                if used_account_ids:
                    borrow_kwargs["exclude_account_ids"] = set(used_account_ids)
                identity, client = stack.enter_context(
                    borrow_yamibo_client(settings, **borrow_kwargs)
                )
                selected_account_id = getattr(identity, "account_id", None)
                if selected_account_id:
                    used_account_ids.add(str(selected_account_id))
                if callable(record_attempt):
                    record_attempt(job.job_id, {"account_id": selected_account_id})
                borrowed_client = True
                fetch_heartbeat.beat()
                url_first_result = None
                url_first_diff = None
                url_first_map: dict[str, str] = {}
                url_first_snapshot = local_snapshot
                url_refresh: dict[str, Any] | None = None
                if strict_selected_scope and not dry_run:
                    url_first_snapshot = _snapshot_with_metadata_targets(local_snapshot, paths, tid, target_urls)
                    url_first_map = _resolve_selected_remote_urls(url_first_snapshot, target_urls, target_positions)
                    if len(url_first_map) == len(target_urls):
                        url_first_diff = _diff_snapshot_images(
                            url_first_snapshot,
                            local_assets=local_assets,
                            paths=paths,
                            include_shared_static=True,
                            scope=scope,
                            include_first_floor=include_first_floor,
                            site_only=site_only,
                            target_urls=set(url_first_map.values()),
                        )
                        if url_first_diff["missing_items_for_apply"]:
                            repo.update_stage(job.job_id, "download_missing_images", progress_current=4, progress_total=6)
                            direct_snapshot = _snapshot_for_missing_images(
                                url_first_snapshot, url_first_diff["missing_items_for_apply"]
                            )
                            worker_cancel_check, main_control_check = _build_download_control_checks(repo, job.job_id)
                            direct_heartbeat = HeartbeatPacer(
                                repo=repo,
                                job_id=job.job_id,
                                worker_id=worker_id,
                                lease_seconds=max(
                                    fetch_lease_seconds,
                                    int(max(float(getattr(settings, "image_download_timeout_seconds", 45.0)) * 3.0, 120.0)),
                                ),
                                min_interval_seconds=max(float(getattr(settings, "worker_heartbeat_seconds", 15)), 1.0),
                            )
                            direct_heartbeat.beat(force=True)

                            def _direct_control() -> None:
                                direct_heartbeat.beat()
                                main_control_check()

                            direct_result = download_images_to_staging(
                                paths,
                                job.job_id,
                                direct_snapshot,
                                timeout=settings.image_download_timeout_seconds,
                                retries=_selected_download_retries(
                                    target_urls=set(target_urls), configured_retries=settings.image_download_retries,
                                ),
                                headers=client.headers,
                                cookie_jar=client.cookie_jar,
                                cookie_file=getattr(client, "cookie_file", None),
                                use_system_proxy=client.use_system_proxy,
                                proxy_url=getattr(client, "proxy_url", None),
                                referer=thread_url_from_tid(tid, base_url=base_url),
                                fetcher=getattr(client, "fetch_image", None),
                                target_urls={str(item["url"]) for item in url_first_diff["missing_items_for_apply"]},
                                cancel_check=worker_cancel_check,
                                control_check=_direct_control,
                                on_progress=_direct_control,
                                stage_deadline_seconds=float(getattr(settings, "image_download_stage_timeout_seconds", 600.0)),
                            )
                            attempted_urls = {str(item["url"]) for item in url_first_diff["missing_items_for_apply"]}
                            failed_urls = attempted_urls - set(direct_result.relative_path_by_url)
                            failed_urls.update(direct_result.missing_urls)
                            failed_urls.update(direct_result.missing_shared_urls)
                            if direct_result.stopped_reason:
                                failed_urls.update(item["url"] for item in url_first_diff["missing_items_for_apply"])
                            needs_url_refresh = _needs_attachment_url_refresh(failed_urls, direct_result.diagnostics)
                            if not needs_url_refresh:
                                url_first_result = direct_result
                            else:
                                url_refresh = {
                                    "reason": "stored_attachment_url_failed",
                                    "initial_diagnostics": [
                                        _safe_image_diagnostic(item)
                                        for item in direct_result.diagnostics
                                        if item.get("status") != "ok"
                                    ],
                                    "resolution": "pending",
                                }
                                try:
                                    repo.assert_lease(job.job_id)
                                    JobEventsRepository(repo.conn).append(
                                        job_id=job.job_id,
                                        event_type="image.url_refresh_requested",
                                        status="partial",
                                        stage="download_missing_images",
                                        payload=url_refresh,
                                    )
                                except LeaseNotAcquired:
                                    raise
                                except Exception:
                                    LOG.warning("Failed to persist URL refresh diagnostics for job %s", job.job_id, exc_info=True)
                                shutil.rmtree(paths.staging_job_dir(job.job_id), ignore_errors=True)

                if url_first_result is not None:
                    snapshot = url_first_snapshot
                    final_url = thread_url_from_tid(tid, base_url=base_url)
                    pages_fetched = 0
                    remote_total_pages = None
                    stopped_reason = None
                else:
                    first_page = client.fetch_thread_page(tid=tid, page=1, base_url=base_url)
                    if max_pages > 1:
                        page_results, remote_total_pages, stopped_reason = client.fetch_thread_pages(
                            tid=tid,
                            base_url=base_url,
                            max_pages=max_pages,
                            first_page=first_page,
                            page_delay_seconds=max(float(getattr(settings, "request_interval_seconds", 1.0)), 1.0),
                            before_each_page=fetch_heartbeat.beat,
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

                fetch_heartbeat.beat()
                if url_first_result is None:
                    repo.update_stage(job.job_id, "diff_images", progress_current=3, progress_total=4)
                selected_url_map = (
                    url_first_map if url_first_result is not None
                    else _resolve_selected_remote_urls(snapshot, target_urls, target_positions)
                    if strict_selected_scope else {}
                )
                if url_refresh is not None:
                    url_refresh["pages_fetched"] = pages_fetched
                    url_refresh["resolution"] = (
                        "not_found" if not selected_url_map else
                        "changed" if any(selected_url_map.get(old) != old for old in target_urls) else
                        "unchanged"
                    )
                    url_refresh["current_targets"] = [
                        _safe_image_diagnostic({"url": url})
                        for url in selected_url_map.values()
                    ]
                selected_remote_urls = set(selected_url_map.values())
                if strict_selected_scope and not selected_remote_urls:
                    repo.fail(
                        job.job_id,
                        "IMAGE_TARGET_NOT_FOUND",
                        "selected image target was not found at its recorded position or stable attachment id",
                        {"tid": tid, "scope": "selected", "target_urls": target_urls,
                         **({"image_url_refresh": url_refresh} if url_refresh is not None else {})},
                    )
                    return
                diff = url_first_diff if url_first_result is not None else _diff_snapshot_images(
                    snapshot,
                    local_assets=local_assets,
                    paths=paths,
                    include_shared_static=True,
                    scope=scope,
                    include_first_floor=include_first_floor,
                    site_only=site_only,
                    target_urls=(
                        selected_remote_urls
                        if strict_selected_scope
                        else (set(target_urls) if target_urls else None)
                    ),
                )

                artifacts: dict[str, Any] = {
                    "dry_run": dry_run,
                    "tid": tid,
                    "base_url": base_url,
                    "remote_access_pattern": "stored_image_urls" if url_first_result is not None else "direct_tid_thread_pages",
                    "remote_fetch": url_first_result is None,
                    "remote_transport": "direct" if direct else "proxy",
                    "direct_fallback_error": direct_fallback_error,
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
                if url_refresh is not None:
                    artifacts["image_url_refresh"] = url_refresh
                if dry_run:
                    repo.update_stage(job.job_id, "finalize", progress_current=4, progress_total=4)
                    repo.succeed(job.job_id, artifacts)
                    return

                if not diff["missing_items_for_apply"]:
                    # The metadata/DB gap that queued this maintenance job may
                    # already have been repaired by another run, or may have
                    # consisted only of static/decorative resources.  Once the
                    # current remote snapshot confirms there is no content
                    # target to fetch, this is a successful no-op rather than
                    # IMAGE_TARGET_NOT_DOWNLOADED.
                    artifacts.update(
                        {
                            "already_complete": True,
                            "remote_fetch": True,
                            "downloaded_image_count": 0,
                            "reconciled_image_count": 0,
                            "download_skipped_reason": "no_missing_content_targets",
                        }
                    )
                    repo.update_stage(job.job_id, "finalize", progress_current=4, progress_total=4)
                    if upgrade_to_full:
                        with transaction(repo.conn):
                            repo.assert_lease(job.job_id, for_update=True)
                            repo.conn.execute(
                                "UPDATE threads SET capture_mode = 'full', archive_status = 'complete', missing_images_json = '[]' WHERE tid = ?",
                                (tid,),
                            )
                    repo.succeed(job.job_id, artifacts)
                    return

                apply_snapshot = _merge_remote_into_local(local_snapshot, snapshot)
                repo.update_stage(job.job_id, "download_missing_images", progress_current=4, progress_total=6)
                missing_snapshot = _snapshot_for_missing_images(snapshot, diff["missing_items_for_apply"])
                worker_cancel_check, main_control_check = _build_download_control_checks(repo, job.job_id)
                download_lease_seconds = max(
                    fetch_lease_seconds,
                    int(max(float(getattr(settings, "image_download_timeout_seconds", 45.0)) * 3.0, 120.0)),
                )
                download_heartbeat = HeartbeatPacer(
                    repo=repo,
                    job_id=job.job_id,
                    worker_id=worker_id,
                    lease_seconds=download_lease_seconds,
                    min_interval_seconds=max(float(getattr(settings, "worker_heartbeat_seconds", 15)), 1.0),
                )
                download_heartbeat.beat(force=True)

                def _download_control() -> None:
                    download_heartbeat.beat()
                    main_control_check()

                def _download_progress() -> None:
                    download_heartbeat.beat()
                    main_control_check()

                image_result = url_first_result or download_images_to_staging(
                    paths,
                    job.job_id,
                    missing_snapshot,
                    timeout=settings.image_download_timeout_seconds,
                    retries=_selected_download_retries(
                        target_urls=set(target_urls) if strict_selected_scope else None,
                        configured_retries=settings.image_download_retries,
                    ),
                    headers=client.headers,
                    cookie_jar=client.cookie_jar,
                    cookie_file=getattr(client, "cookie_file", None),
                    use_system_proxy=client.use_system_proxy,
                    proxy_url=getattr(client, "proxy_url", None),
                    referer=final_url,
                    fetcher=getattr(client, "fetch_image", None),
                    target_urls={str(item["url"]) for item in diff["missing_items_for_apply"]},
                    cancel_check=worker_cancel_check,
                    control_check=_download_control,
                    on_progress=_download_progress,
                    stage_deadline_seconds=float(getattr(settings, "image_download_stage_timeout_seconds", 600.0)),
                )
                diagnostics = [_safe_image_diagnostic(item) for item in image_result.diagnostics]
                error_type_counts: dict[str, int] = {}
                http_status_counts: dict[str, int] = {}
                for item in diagnostics:
                    if item.get("error_type"):
                        key = str(item["error_type"])
                        error_type_counts[key] = error_type_counts.get(key, 0) + 1
                    if item.get("http_status") is not None:
                        key = str(item["http_status"])
                        http_status_counts[key] = http_status_counts.get(key, 0) + 1
                diagnostic_summary = {
                    "attempted": len(diagnostics),
                    "succeeded": sum(1 for item in diagnostics if item.get("status") == "ok"),
                    "failed": sum(1 for item in diagnostics if item.get("status") != "ok"),
                    "retryable_failed": sum(
                        1
                        for item in diagnostics
                        if item.get("status") != "ok"
                        and (item.get("retryable") or item.get("error_type") == "waf_response")
                    ),
                    "transports": sorted({str(item.get("transport")) for item in diagnostics}),
                    "error_type_counts": error_type_counts,
                    "http_status_counts": http_status_counts,
                    "stopped_reason": image_result.stopped_reason,
                }
                should_retry_image_download = _should_retry_image_download(image_result, diagnostics)
                image_failure_code = _image_download_failure_code(diagnostics)
                diagnostic_summary["failure_code"] = (
                    image_failure_code if any(item.get("status") != "ok" for item in diagnostics) else None
                )
                artifacts["image_download_diagnostics"] = diagnostics
                artifacts["image_download_summary"] = diagnostic_summary
                has_failures = bool(
                    image_result.missing_urls
                    or image_result.missing_shared_urls
                    or image_result.stopped_reason
                )
                event_payload = {"tid": tid, "summary": diagnostic_summary, "diagnostics": diagnostics}
                try:
                    repo.assert_lease(job.job_id)
                    JobEventsRepository(repo.conn).append(
                        job_id=job.job_id,
                        event_type="image.download.result",
                        status="partial" if has_failures else "succeeded",
                        stage="download_missing_images",
                        payload=event_payload,
                    )
                except LeaseNotAcquired:
                    raise
                except Exception:
                    LOG.warning("Failed to persist image download diagnostics for job %s", job.job_id, exc_info=True)
                first_failure = next((item for item in diagnostics if item.get("status") != "ok"), None)
                emit(LOG, logging.INFO if not has_failures else logging.WARNING, "image.download.result",
                     "image download result", result="success" if not has_failures else "partial",
                     status="ok" if not has_failures else "error", job_id=job.job_id, tid=tid,
                     error_code=None if not has_failures else image_failure_code,
                     error_message=None if first_failure is None else str(first_failure.get("error_message") or first_failure.get("error_type") or "image download failed"),
                     retryable=bool(diagnostic_summary["retryable_failed"]), tags=["image", "download"], operation="image_backfill",
                     payload=event_payload)
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
                    missing_image_urls, missing_shared_image_urls = _missing_urls_from_assets(
                        synced_assets,
                        include_shared=not site_only,
                    )
                else:
                    missing_image_urls = _unique([
                        *[url for url in previous_missing if not _url_was_successfully_downloaded(url, successful_urls)],
                        *image_result.missing_urls,
                    ])
                    missing_shared_image_urls = _unique([
                        *[url for url in previous_missing_shared if not _url_was_successfully_downloaded(url, successful_urls)],
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
                    # Do not let an expired handler mutate the archive after
                    # another worker has reclaimed this job.
                    repo.assert_lease(job.job_id, for_update=True)
                    if url_first_result is not None:
                        repo.conn.execute(
                            "UPDATE threads SET archive_status = ?, missing_images_json = ?, capture_mode = ? WHERE tid = ?",
                            (archive_status, json.dumps([*missing_image_urls, *missing_shared_image_urls], ensure_ascii=False),
                             thread["capture_mode"], tid),
                        )
                    else:
                        ThreadsRepository(repo.conn).upsert_snapshot(
                            apply_snapshot,
                            forum_id=thread["forum_id"] if "forum_id" in thread.keys() else None,
                            category=thread["category"] if "category" in thread.keys() else None,
                            context_path=str(paths.thread_context(tid).relative_to(settings.data_dir)),
                            archive_status=archive_status,
                            capture_mode=thread["capture_mode"],
                            missing_image_urls=[*missing_image_urls, *missing_shared_image_urls],
                        )
                    if selected_scope:
                        _update_selected_assets(
                            repo.conn,
                            tid=apply_snapshot.tid,
                            remote_assets=synced_assets,
                            local_assets=local_assets,
                            local_path_by_url=local_path_by_url,
                            selected_url_map=selected_url_map,
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
                if upgrade_to_full:
                    with transaction(repo.conn):
                        repo.assert_lease(job.job_id, for_update=True)
                        repo.conn.execute("UPDATE threads SET capture_mode = 'full' WHERE tid = ?", (tid,))
                artifacts.update(
                    {
                        "context_path": str(context_path),
                        "metadata_path": str(metadata_path),
                        "archive_status_after": archive_status,
                        "capture_mode": "full" if upgrade_to_full else thread["capture_mode"],
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
                if strict_selected_scope and not resolved_selected_urls:
                    if should_retry_image_download:
                        retry_delay = _image_retry_delay_seconds(diagnostics, retry_count=job.retry_count)
                        retry_scheduled = repo.retry_later(
                            job.job_id,
                            error_code=image_failure_code,
                            error_message=(
                                "remote image request was blocked by WAF; retrying with a fresh remote attempt"
                                if image_failure_code == "REMOTE_SOFT_BLOCK"
                                else "selected image target download was transiently unsuccessful; retrying"
                            ),
                            artifacts=artifacts,
                            delay_seconds=retry_delay,
                            max_delay_seconds=3600 if retry_delay is not None else 60,
                        )
                        if retry_scheduled:
                            shutil.rmtree(paths.staging_job_dir(job.job_id), ignore_errors=True)
                            return
                    repo.fail(
                        job.job_id,
                        image_failure_code,
                        (
                            "remote image request was blocked by WAF; selected target was not confirmed missing"
                            if image_failure_code == "REMOTE_SOFT_BLOCK"
                            else "selected image target was not downloaded or reconciled"
                        ),
                        artifacts,
                    )
                elif strict_selected_scope and set(selected_remote_urls or target_urls).issubset(resolved_selected_urls):
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
            if _switch_to_proxy_after_direct_failure(exc):
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
        except Exception as exc:  # noqa: BLE001 - direct transport may need a proxy fallback
            if _switch_to_proxy_after_direct_failure(exc):
                continue
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


def _metadata_target_positions(paths: StoragePaths, tid: int) -> dict[str, dict[str, Any]]:
    metadata = _load_archive_metadata(paths, tid)
    positions: dict[str, dict[str, Any]] = {}
    for floor in metadata.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        for index, raw_url in enumerate(floor.get("remote_image_urls") or floor.get("image_urls") or [], start=1):
            url = str(raw_url)
            if url:
                positions[url] = {
                    "pid": floor.get("pid"), "floor_no": floor.get("floor_no"), "image_index": index,
                }
    return positions


def _metadata_missing_targets(paths: StoragePaths, tid: int, *, site_only: bool = False) -> list[str]:
    metadata = _load_archive_metadata(paths, tid)
    targets: list[str] = []
    for floor in metadata.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        urls = [str(url) for url in floor.get("remote_image_urls") or floor.get("image_urls") or []]
        slots = floor.get("image_slots") or []
        if not urls and isinstance(slots, list):
            urls = [
                str(slot.get("remote_url") or "")
                for slot in slots
                if isinstance(slot, dict) and slot.get("remote_url")
            ]
        for index, url in enumerate(urls):
            if site_only and not _is_yamibo_site_content_url(url):
                continue
            slot = slots[index] if index < len(slots) and isinstance(slots[index], dict) else {}
            status = str(slot.get("status") or "").strip().lower()
            local_path = str(slot.get("local_path") or "")
            path_missing = bool(local_path) and not _is_valid_image_file(
                _asset_path(tid=tid, local_path=local_path, paths=paths)
            )
            if (
                status != "skipped"
                and (
                    status in {"missing", "missing_shared", "pending"}
                    or not local_path
                    or path_missing
                )
            ):
                targets.append(url)
    return _unique(targets)


def _reconcile_missing_targets(
    repo_or_conn,
    *,
    job_id: str | None = None,
    paths: StoragePaths,
    tid: int,
    target_urls: list[str],
) -> set[str]:
    """Reconcile rotated attachment signatures from valid local assets, without HTTP."""
    repo = repo_or_conn if isinstance(repo_or_conn, JobsRepository) else JobsRepository(repo_or_conn)
    conn = repo.conn
    if not target_urls:
        return set()
    rows = AssetsRepository(conn).list_assets(tid)
    positions = _metadata_target_positions(paths, tid)
    used: set[str] = set()
    reconciled: set[str] = set()
    selected_rows: list[tuple[str, Any]] = []
    for url in target_urls:
        identity = remote_image_identity(url)
        position = positions.get(url) or {}
        pid = position.get("pid")
        candidates = [
            row for row in rows
            if str(row["asset_type"]) in {"image", "attachment"}
            and identity is not None
            and remote_image_identity(str(row["remote_url"])) == identity
            and str(row["asset_id"]) not in used
            and (pid is None or int(row["pid"]) == int(pid))
        ]
        row = next((candidate for candidate in candidates if candidate["local_path"]), None)
        if row is None:
            continue
        path = _asset_path(tid=tid, local_path=str(row["local_path"]), paths=paths)
        if not _is_valid_image_file(path):
            continue
        selected_rows.append((url, row))
        used.add(str(row["asset_id"]))
        reconciled.add(url)
    if not reconciled:
        return reconciled
    metadata = _load_archive_metadata(paths, tid)
    with transaction(conn):
        if job_id:
            repo.assert_lease(job_id, for_update=True)
        for url, row in selected_rows:
            conn.execute(
                "UPDATE assets SET remote_url = ?, status = 'downloaded' WHERE tid = ? AND asset_id = ?",
                (url, tid, row["asset_id"]),
            )
        asset_rows = AssetsRepository(conn).list_assets(tid)
        missing, missing_shared = _refresh_metadata_from_asset_rows(
            paths=paths, tid=tid, metadata=metadata, asset_rows=asset_rows,
        )
        conn.execute(
            "UPDATE threads SET missing_images_json = ?, archive_status = ? WHERE tid = ?",
            (json.dumps([*missing, *missing_shared], ensure_ascii=False), metadata["archive_status"], tid),
        )
    atomic_write_text(paths.thread_metadata(tid), json.dumps(metadata, ensure_ascii=False, indent=2))
    return reconciled


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
    repo_or_conn,
    *,
    job_id: str | None = None,
    paths: StoragePaths,
    tid: int,
    target_urls: list[str],
    target_positions: dict[str, dict[str, Any]],
    expected_paths: dict[str, Path],
    target_asset_id: str,
) -> set[str]:
    """Repair DB/metadata from an already-valid expected file without HTTP."""
    repo = repo_or_conn if isinstance(repo_or_conn, JobsRepository) else JobsRepository(repo_or_conn)
    conn = repo.conn
    if not expected_paths:
        return set()
    asset_rows = AssetsRepository(conn).list_assets(tid)
    asset_by_url = {str(row["remote_url"]): row for row in asset_rows if row["remote_url"]}
    metadata = _load_archive_metadata(paths, tid)
    reconciled: set[str] = set()
    selected_rows: list[tuple[str, str, Any]] = []
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
        selected_rows.append((url, relative_path, row))
        reconciled.add(url)

    if not reconciled:
        return reconciled
    with transaction(conn):
        if job_id:
            repo.assert_lease(job_id, for_update=True)
        for url, relative_path, row in selected_rows:
            conn.execute(
                "UPDATE assets SET local_path = ?, status = 'downloaded' WHERE tid = ? AND asset_id = ? AND remote_url = ?",
                (relative_path, tid, row["asset_id"], url),
            )
        asset_rows = AssetsRepository(conn).list_assets(tid)
        missing, missing_shared = _refresh_metadata_from_asset_rows(
            paths=paths,
            tid=tid,
            metadata=metadata,
            asset_rows=asset_rows,
        )
        conn.execute(
            "UPDATE threads SET missing_images_json = ?, archive_status = ? WHERE tid = ?",
            (json.dumps([*missing, *missing_shared], ensure_ascii=False), metadata["archive_status"], tid),
        )
    atomic_write_text(paths.thread_metadata(tid), json.dumps(metadata, ensure_ascii=False, indent=2))
    return reconciled


def _refresh_metadata_from_asset_rows(*, paths: StoragePaths, tid: int, metadata: dict[str, Any], asset_rows) -> tuple[list[str], list[str]]:
    assets_by_identity = {
        remote_image_identity(str(row["remote_url"])): row
        for row in asset_rows
        if row["remote_url"]
    }
    missing_images: list[str] = []
    missing_shared: list[str] = []
    archived_images: dict[str, list[str]] = {}
    non_export_images: dict[str, list[str]] = {}
    shared_images: dict[str, list[str]] = {}
    for floor in metadata.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        pid_key = str(floor.get("pid"))
        remote_urls = [str(url) for url in (floor.get("remote_image_urls") or [])]
        slots: list[dict[str, str | None]] = []
        for url in remote_urls:
            image_class = _classify_backfill_image(url)
            row = assets_by_identity.get(remote_image_identity(url))
            local_path = str(row["local_path"]) if row is not None and row["local_path"] else None
            if local_path and _is_valid_image_file(_asset_path(tid=tid, local_path=local_path, paths=paths)):
                if image_class in {"static", "decorative"}:
                    status = "shared"
                    shared_images.setdefault(pid_key, []).append(local_path)
                elif bool(row["exportable"]):
                    status = "content"
                    archived_images.setdefault(pid_key, []).append(local_path)
                else:
                    status = "non_export"
                    non_export_images.setdefault(pid_key, []).append(local_path)
            elif image_class == "embedded":
                local_path = None
                status = "skipped"
            else:
                local_path = None
                status = "missing_shared" if image_class in {"static", "decorative"} else "missing"
                (missing_shared if status == "missing_shared" else missing_images).append(url)
            slots.append({"remote_url": url, "local_path": local_path, "status": status})
        floor["image_slots"] = slots
        floor["content_image_urls"] = list(archived_images.get(pid_key, []))
        floor["non_export_image_urls"] = list(non_export_images.get(pid_key, []))
        floor["shared_image_urls"] = list(shared_images.get(pid_key, []))
        floor["image_urls"] = [*floor["content_image_urls"], *floor["non_export_image_urls"]]
        floor["missing_image_urls"] = [
            slot["remote_url"] for slot in slots if slot["status"] in {"missing", "missing_shared"}
        ]
    metadata["archived_images"] = archived_images
    metadata["non_export_images"] = non_export_images
    metadata["shared_images"] = shared_images
    metadata["missing_image_urls"] = _unique(missing_images)
    metadata["missing_shared_image_urls"] = _unique(missing_shared)
    metadata["archive_status"] = "partial" if missing_images or missing_shared else "complete"
    return metadata["missing_image_urls"], metadata["missing_shared_image_urls"]


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


def _prefers_direct_transport(job: Job) -> bool:
    """Interactive image repair starts direct; automatic idle scans keep pool routing."""
    payload = job.payload if isinstance(job.payload, dict) else {}
    if bool(payload.get("internal_auto")):
        return False
    preference = str(payload.get("transport_preference") or "").strip().lower()
    if preference == "direct":
        return True
    if str(payload.get("priority") or "").strip().lower() == "interactive":
        return True
    # Older manually-created selected jobs predate transport_preference.
    return str(payload.get("scope") or "").strip().lower() == "selected"


def _remote_attempt_history(repo: JobsRepository, job_id: str) -> dict[str, Any]:
    try:
        current = repo.get(job_id)
        attempt = current.artifacts.get("remote_attempt") if isinstance(current.artifacts, dict) else None
        return attempt if isinstance(attempt, dict) else {}
    except Exception:  # noqa: BLE001 - selection diagnostics must not mask the job
        LOG.debug("Unable to read remote attempt history for image_backfill job %s", job_id, exc_info=True)
        return {}


def _attempted_account_ids(repo: JobsRepository, job_id: str) -> set[str]:
    values = _remote_attempt_history(repo, job_id).get("account_ids_tried")
    return {str(value) for value in values if value} if isinstance(values, list) else set()


def _select_backfill_proxy(
    settings: Settings,
    repo: JobsRepository,
    *,
    tid: int,
    job_id: str,
    exclude_nodes: set[str] | None = None,
) -> tuple[object | None, dict[str, object]]:
    history = _remote_attempt_history(repo, job_id)
    tried = history.get("nodes_tried")
    excluded = {str(value) for value in tried if value} if isinstance(tried, list) else set()
    excluded.update(str(value) for value in (exclude_nodes or set()) if value)
    if excluded:
        binding = select_thread_proxy(settings, tid=tid, job_id=job_id, exclude_nodes=excluded)
    else:
        binding = select_thread_proxy(settings, tid=tid, job_id=job_id)
    return binding, _proxy_pool_artifacts(settings, binding)


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
    return _merge_thread_snapshots(snapshots)


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


def _snapshot_with_metadata_targets(snapshot: ThreadSnapshot, paths: StoragePaths, tid: int, target_urls: list[str]) -> ThreadSnapshot:
    """Restore missing URL slots omitted by DB-only asset reconstruction."""
    metadata = _load_archive_metadata(paths, tid)
    targets = set(target_urls)
    floors_by_pid: dict[int, dict[str, Any]] = {}
    for floor in metadata.get("floors") or []:
        if not isinstance(floor, dict):
            continue
        try:
            floors_by_pid[int(floor["pid"])] = floor
        except (KeyError, TypeError, ValueError):
            continue
    floors = []
    for floor in snapshot.floors:
        saved = floors_by_pid.get(floor.pid) or {}
        urls = [str(url) for url in (saved.get("remote_image_urls") or []) if str(url)]
        identities = {remote_image_identity(url) for url in urls}
        if targets.intersection(urls) and all(
            remote_image_identity(current) in identities
            for current in floor.image_urls
        ):
            floors.append(replace(floor, image_urls=urls, has_images=True))
        else:
            floors.append(floor)
    return replace(snapshot, floors=floors)


def _merge_remote_into_local(local_snapshot, remote_snapshot):
    remote_by_pid = {floor.pid: floor for floor in remote_snapshot.floors}
    merged_floors = []
    seen_pids: set[int] = set()
    for floor in local_snapshot.floors:
        remote_floor = remote_by_pid.get(floor.pid)
        if remote_floor is None:
            merged = floor
        else:
            image_urls = list(remote_floor.image_urls or floor.image_urls)
            merged = replace(
                floor,
                has_images=bool(image_urls) or floor.has_images or remote_floor.has_images,
                image_urls=image_urls,
                rich_body_html=remote_floor.rich_body_html or floor.rich_body_html,
            )
        merged_floors.append(merged)
        seen_pids.add(floor.pid)
    next_floor_no = len(merged_floors) + 1
    for floor in remote_snapshot.floors:
        if floor.pid not in seen_pids:
            merged_floors.append(replace(floor, floor_no=next_floor_no))
            seen_pids.add(floor.pid)
            next_floor_no += 1
    return replace(
        local_snapshot,
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
    site_only: bool = False,
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
            if site_only and not _is_yamibo_site_url(url):
                continue
            image_class = _classify_backfill_image(url)
            if site_only and image_class in {"static", "decorative"}:
                continue
            # Automatic idle repair intentionally remains restricted to known
            # first-party content assets.  A user-selected external URL may
            # still be attempted through the interactive endpoint.
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
            ) or (
                submitted_aid is None
                and urlparse(submitted).path.lower().startswith("/data/attachment/")
                and _is_site_attachment(submitted)
                and _is_site_attachment(candidate)
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


def _url_was_successfully_downloaded(url: str, successful_urls: set[str]) -> bool:
    """Match rotated Yamibo attachment signatures by stable attachment identity."""
    identity = remote_image_identity(url)
    return any(remote_image_identity(candidate) == identity for candidate in successful_urls)


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
    selected_url_map: dict[str, str] | None = None,
) -> None:
    """Update only selected rows, retaining every other asset untouched."""
    downloaded = set(local_path_by_url)
    rows = AssetsRepository(conn).list_assets(tid)
    for asset in remote_assets:
        if asset.remote_url not in downloaded:
            continue
        old = _local_asset_for_url(local_assets, asset.remote_url)
        if old is None:
            old = next(
                (
                    local_assets[submitted]
                    for submitted, current in (selected_url_map or {}).items()
                    if current == asset.remote_url and submitted in local_assets
                ),
                None,
            )
        if old is None:
            local_path = local_path_by_url.get(asset.remote_url)
            if local_path:
                conn.execute(
                    """INSERT INTO assets
                       (asset_id, tid, pid, asset_type, remote_url, local_path, exportable, required, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'downloaded')""",
                    (asset.asset_id, tid, asset.pid, asset.asset_type, asset.remote_url,
                     local_path, asset.exportable, asset.required),
                )
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


def _missing_urls_from_assets(
    assets,
    *,
    include_shared: bool = True,
) -> tuple[list[str], list[str]]:
    missing_images: list[str] = []
    missing_shared: list[str] = []
    for asset in assets:
        if asset.local_path or _classify_backfill_image(asset.remote_url) == "embedded":
            continue
        if _classify_backfill_image(asset.remote_url) in {"static", "decorative"}:
            if include_shared:
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
    if _is_yamibo_site_url(url) and parsed.path.lower().startswith("/static/image/"):
        if parsed.path.endswith("/common/back.gif"):
            return "decorative"
        return "static"
    if _is_yamibo_site_url(url):
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
    if (parsed.hostname or "").lower() != "bbs.yamibo.com":
        return False
    if parsed.path.lower().startswith("/data/attachment/"):
        return True
    if parsed.path.lower() != "/forum.php":
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
