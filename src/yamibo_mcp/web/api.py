from __future__ import annotations

from urllib.parse import parse_qs
from http import HTTPStatus

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from .routes._helpers import error_response
from .routes.settings import handle_settings_get, handle_settings_update, handle_settings_models_get
from .routes.dashboard import handle_dashboard
from .routes.jobs import (
    handle_jobs_list, handle_jobs_counts, handle_jobs_failure_counts,
    handle_job_detail, handle_job_events, handle_delete_job, handle_retry_job,
    handle_pause_job, handle_resume_job, handle_job_control,
    handle_batch_delete_jobs, handle_batch_delete_jobs_by_ids,
    handle_safe_delete_job, handle_resume_remote_access,
)
from .routes.threads import (
    handle_threads_list, handle_thread_detail, handle_thread_images,
    handle_thread_assets, handle_thread_blocks, handle_thread_update_check,
    handle_resync_thread, handle_resync_threads_batch,
    handle_update_thread, handle_export_thread,
    handle_delete_thread, handle_batch_delete_threads,
    handle_update_chapter, handle_archive_threads_batch,
    handle_exports_list,
)
from .routes.series import (
    handle_series_list, handle_series_detail,
    handle_delete_series, handle_similar_series,
)
from .routes.forums import handle_forums_list, handle_refresh_forum_size_cache, handle_fonts_list
from .routes.rag import (
    handle_rag_overview, handle_rag_threads,
    handle_rag_index, handle_rag_index_batch, handle_rag_search,
)
from .routes.review import (
    handle_review_items, handle_confirm_series, handle_merge_series,
    handle_confirm_title, handle_rebuild_series,
    handle_update_title, handle_update_series,
)
from .routes.debug import handle_debug_info, handle_logs
from .routes.remote_forum import (
    handle_remote_forums_list, handle_remote_forum_browse, handle_remote_thread_detail,
    handle_remote_image_proxy,
)


def handle_api(handler, path: str, query: str, settings: Settings) -> bool:
    """Handle /api/* routes. Returns True if handled."""
    if not path.startswith("/api/"):
        return False

    route = path[4:]  # strip /api
    params = parse_qs(query)
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        _route(handler, route, params, conn, settings)
    except (BrokenPipeError, ConnectionResetError):
        return True
    except Exception as exc:
        error_response(handler, str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
    finally:
        conn.close()
    return True


def _route(handler, route: str, params, conn, settings):
    if route == "/dashboard":
        handle_dashboard(handler, conn, params, settings)
    elif route == "/jobs" and handler.command == "GET":
        handle_jobs_list(handler, params, conn)
    elif route == "/jobs/counts" and handler.command == "GET":
        handle_jobs_counts(handler, conn)
    elif route == "/jobs/failure-counts" and handler.command == "GET":
        handle_jobs_failure_counts(handler, params, conn)
    elif route.startswith("/jobs/") and route.endswith("/events"):
        job_id = route[6:-7]
        handle_job_events(handler, job_id, conn)
    elif route.startswith("/jobs/") and handler.command == "GET":
        job_id = route[6:]
        handle_job_detail(handler, job_id, conn)
    elif route == "/jobs/delete" and handler.command == "POST":
        handle_delete_job(handler, conn)
    elif route == "/jobs/retry" and handler.command == "POST":
        handle_retry_job(handler, conn)
    elif route == "/jobs/pause" and handler.command == "POST":
        handle_pause_job(handler, conn)
    elif route == "/jobs/resume" and handler.command == "POST":
        handle_resume_job(handler, conn)
    elif route == "/jobs/control" and handler.command == "POST":
        handle_job_control(handler, conn, settings)
    elif route == "/jobs/batch-delete" and handler.command == "POST":
        handle_batch_delete_jobs(handler, conn)
    elif route == "/jobs/batch-delete-ids" and handler.command == "POST":
        handle_batch_delete_jobs_by_ids(handler, conn)
    elif route == "/jobs/safe-delete" and handler.command == "POST":
        handle_safe_delete_job(handler, conn)
    elif route == "/threads" and handler.command == "GET":
        handle_threads_list(handler, params, conn)
    elif route.startswith("/threads/") and route.endswith("/images"):
        tid = int(route[9:-7])
        handle_thread_images(handler, tid, conn, settings)
    elif route.startswith("/threads/") and route.endswith("/assets"):
        tid = int(route[9:-7])
        handle_thread_assets(handler, tid, conn)
    elif route.startswith("/threads/") and route.endswith("/blocks"):
        tid = int(route[9:-7])
        handle_thread_blocks(handler, tid, conn)
    elif route.startswith("/threads/") and route.endswith("/update-check") and handler.command == "GET":
        tid = int(route[9:-13])
        handle_thread_update_check(handler, tid, settings)
    elif route.startswith("/threads/") and handler.command == "GET":
        tid = int(route[9:])
        handle_thread_detail(handler, tid, conn, params, settings)
    elif route == "/series" and handler.command == "GET":
        handle_series_list(handler, conn)
    elif route == "/series/delete" and handler.command == "POST":
        handle_delete_series(handler, conn, settings)
    elif route.startswith("/series/") and route.endswith("/similar"):
        series_id = int(route[8:-8])
        handle_similar_series(handler, series_id, conn)
    elif route.startswith("/series/") and handler.command == "GET":
        series_id = int(route[8:])
        handle_series_detail(handler, series_id, conn)
    elif route == "/forums" and handler.command == "GET":
        handle_forums_list(handler, conn, settings)
    elif route == "/forums/refresh-size-cache" and handler.command == "POST":
        handle_refresh_forum_size_cache(handler, conn, settings)
    elif route == "/exports" and handler.command == "GET":
        handle_exports_list(handler, conn)
    elif route == "/fonts" and handler.command == "GET":
        handle_fonts_list(handler, settings)
    elif route == "/rag/overview" and handler.command == "GET":
        handle_rag_overview(handler, conn, settings)
    elif route == "/rag/threads" and handler.command == "GET":
        handle_rag_threads(handler, params, conn)
    elif route == "/rag/index" and handler.command == "POST":
        handle_rag_index(handler)
    elif route == "/rag/index-batch" and handler.command == "POST":
        handle_rag_index_batch(handler, conn)
    elif route == "/rag/search" and handler.command == "POST":
        handle_rag_search(handler)
    elif route == "/review" and handler.command == "GET":
        handle_review_items(handler, conn)
    elif route == "/review/confirm-series" and handler.command == "POST":
        handle_confirm_series(handler, conn, settings)
    elif route == "/review/merge-series" and handler.command == "POST":
        handle_merge_series(handler, conn, settings)
    elif route == "/review/confirm-title" and handler.command == "POST":
        handle_confirm_title(handler, conn, settings)
    elif route == "/review/rebuild-series" and handler.command == "POST":
        handle_rebuild_series(handler, conn, settings)
    elif route == "/review/update-title" and handler.command == "POST":
        handle_update_title(handler, conn, settings)
    elif route == "/threads/update-chapter" and handler.command == "POST":
        handle_update_chapter(handler, conn)
    elif route == "/review/update-series" and handler.command == "POST":
        handle_update_series(handler, conn, settings)
    elif route == "/threads/resync" and handler.command == "POST":
        handle_resync_thread(handler, conn)
    elif route == "/threads/resync-batch" and handler.command == "POST":
        handle_resync_threads_batch(handler, conn)
    elif route == "/threads/archive-batch" and handler.command == "POST":
        handle_archive_threads_batch(handler)
    elif route == "/threads/update" and handler.command == "POST":
        handle_update_thread(handler, conn)
    elif route == "/threads/export" and handler.command == "POST":
        handle_export_thread(handler, conn, settings)
    elif route == "/remote-access/resume" and handler.command == "POST":
        handle_resume_remote_access(handler, conn)
    elif route == "/threads/delete" and handler.command == "POST":
        handle_delete_thread(handler, conn, settings)
    elif route == "/threads/batch-delete" and handler.command == "POST":
        handle_batch_delete_threads(handler, conn, settings)
    elif route == "/settings" and handler.command == "GET":
        handle_settings_get(handler, settings)
    elif route == "/settings" and handler.command == "POST":
        handle_settings_update(handler, settings)
    elif route == "/settings/models" and handler.command == "GET":
        handle_settings_models_get(handler, settings)
    elif route == "/debug/info" and handler.command == "GET":
        handle_debug_info(handler, conn, settings)
    elif route == "/logs" and handler.command == "GET":
        handle_logs(handler, params)
    elif route == "/remote/forums" and handler.command == "GET":
        handle_remote_forums_list(handler, conn, settings)
    elif route == "/remote/forum" and handler.command == "GET":
        handle_remote_forum_browse(handler, params, conn, settings)
    elif route == "/remote/image" and handler.command == "GET":
        handle_remote_image_proxy(handler, params, settings)
    elif route.startswith("/remote/threads/") and handler.command == "GET":
        tid = int(route[16:])
        handle_remote_thread_detail(handler, tid, params, conn, settings)
    else:
        error_response(handler, f"Not found: {route}", HTTPStatus.NOT_FOUND)
