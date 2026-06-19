from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import replace

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import Job
from yamibo_mcp.domain.validation import validate_thread_snapshot
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.storage.images import download_images_to_staging
from yamibo_mcp.storage.staging import write_staging_failure, write_staging_snapshot, write_staging_title_parse_log
from yamibo_mcp.storage.thread_archive import materialize_thread
from yamibo_mcp.services.title_hints import update_title_hints
from yamibo_mcp.services.title_llm import refine_title_parse_with_llm, title_parse_to_dict
from yamibo_mcp.yamibo.client import YamiboClient
from yamibo_mcp.yamibo.parsers.thread_detail import parse_thread_snapshot
from yamibo_mcp.yamibo.urls import extract_tid_from_input

LOG = logging.getLogger(__name__)


def handle_sync_thread(repo: JobsRepository, job: Job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
    paths = StoragePaths(settings.data_dir, export_dir=settings.export_dir)
    html_path_value = job.payload.get("html_path")
    url_value = job.payload.get("url")
    base_url = job.payload.get("base_url")
    tid = job.tid or extract_tid_from_input(job.payload.get("tid", ""))
    source_url: str | None = None
    snapshot = None
    client: YamiboClient | None = None
    title_parse_log: dict[str, object] | None = None

    try:
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
            client = YamiboClient(
                cookie_file=str(cookie_path),
                use_system_proxy=settings.use_system_proxy,
                login_username=settings.login_username,
                login_password=settings.login_password,
            )
            fetched = client.fetch_thread(tid=tid, url=str(url_value) if url_value else None, base_url=str(base_url) if base_url else None)
            html = fetched.html
            source_url = fetched.final_url

        repo.update_stage(job.job_id, "parse", progress_current=1, progress_total=6)
        snapshot = parse_thread_snapshot(html, url=source_url, tid=tid)
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
        repo.heartbeat(job.job_id, worker_id, lease_seconds)

        repo.update_stage(job.job_id, "staging", progress_current=2, progress_total=6)
        # 先把解析出的快照写入 staging，后面即使校验或落库失败，也能留下排查材料。
        write_staging_snapshot(paths, job.job_id, snapshot)

        repo.update_stage(job.job_id, "validate", progress_current=3, progress_total=6)
        validation = validate_thread_snapshot(snapshot)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))

        # 图片下载先走 staging，失败 URL 先记录下来，后续再演进成 partial 状态。
        repo.update_stage(job.job_id, "download_images", progress_current=4, progress_total=6)
        image_result = download_images_to_staging(
            paths,
            job.job_id,
            snapshot,
            timeout=settings.image_download_timeout_seconds if client is not None else settings.image_download_timeout_seconds,
            retries=settings.image_download_retries,
            headers=None if client is None else client.headers,
            cookie_jar=None if client is None else client.cookie_jar,
            use_system_proxy=False if client is None else client.use_system_proxy,
            referer=source_url,
        )

        repo.update_stage(job.job_id, "db_commit", progress_current=5, progress_total=6)
        context_path = paths.thread_context(snapshot.tid)
        relative_context = str(context_path.relative_to(settings.data_dir))
        archive_status = "partial" if image_result.missing_urls else "complete"
        with transaction(repo.conn):
            ThreadsRepository(repo.conn).upsert_snapshot(
                snapshot,
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
            "missing_image_count": len(image_result.missing_urls),
            "missing_shared_image_count": len(image_result.missing_shared_urls),
            "archive_status": archive_status,
        }
        if llm_title_meta is not None:
            artifacts["title_llm"] = {
                "attempted": bool(llm_title_meta.get("attempted")),
                "used": bool(llm_title_meta.get("used")),
                "model": llm_title_meta.get("model"),
                "strategy": llm_title_meta.get("strategy"),
                "error": llm_title_meta.get("error"),
            }
        if image_result.missing_urls:
            update_title_hints(
                settings,
                group_name=snapshot.title.group_name,
                author_guess=snapshot.title.author_guess,
            )
            # 图片不完整时保留正式归档，但线程状态必须明确标成 partial，不能伪装成 complete。
            repo.conn.execute(
                """
                UPDATE jobs
                SET status = ?, stage = 'finalize', progress_current = COALESCE(progress_total, progress_current),
                    artifacts_json = ?, updated_at = CURRENT_TIMESTAMP, finished_at = CURRENT_TIMESTAMP,
                    lease_until = NULL
                WHERE job_id = ?
                """,
                ("partial", json.dumps(artifacts, ensure_ascii=False), job.job_id),
            )
            repo.conn.commit()
        else:
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
