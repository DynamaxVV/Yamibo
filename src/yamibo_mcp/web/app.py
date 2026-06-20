from __future__ import annotations

import argparse
import html
import json
import mimetypes
import shutil
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.services.title_hints import update_title_hints
from yamibo_mcp.storage.paths import StoragePaths

TRANSLATIONS = {
    "zh": {
        "dashboard": "控制台",
        "jobs": "任务",
        "threads": "帖子归档",
        "series": "作品系列",
        "title_review": "标题复核",
        "exports": "导出",
        "language": "语言",
        "switch_zh": "中文",
        "switch_en": "English",
        "data_dir": "数据目录",
        "create_noop_job": "创建空任务",
        "local_html_path": "本地 HTML 路径",
        "create_sync_job": "创建同步任务",
        "recent_jobs": "最近任务",
        "worker_heartbeats": "Worker 心跳",
        "recent_audit_events": "最近审计事件",
        "id": "ID",
        "type": "类型",
        "status": "状态",
        "stage": "阶段",
        "error": "错误",
        "updated": "更新时间",
        "worker": "Worker",
        "latest_heartbeat": "最近心跳",
        "latest_update": "最近更新",
        "running_jobs": "运行中任务",
        "seen_jobs": "处理过任务数",
        "action": "动作",
        "target": "目标",
        "actor": "执行者",
        "at": "时间",
        "status_filters": "状态筛选",
        "all": "全部",
        "queued": "排队中",
        "running": "运行中",
        "succeeded": "成功",
        "failed": "失败",
        "interrupted": "中断",
        "progress": "进度",
        "error_code": "错误码",
        "error_message": "错误信息",
        "created": "创建时间",
        "finished": "完成时间",
        "created_job": "已创建任务",
        "search": "搜索",
        "title": "标题",
        "core_title": "核心标题",
        "chapter": "章节",
        "series_key": "系列键",
        "archive": "归档状态",
        "validation": "校验状态",
        "synced": "同步时间",
        "search_placeholder": "搜索标题、作者、汉化组、正文",
        "canonical_title": "规范标题",
        "author": "作者",
        "confidence": "置信度",
        "needs_review": "需要复核",
        "last_sync": "最近同步",
        "aliases": "别名",
        "thread_titles": "帖子标题复核",
        "edit_title_fields": "手动修正标题信息",
        "confirm_title": "确认标题",
        "save_title_review": "保存并确认",
        "confirm_series": "确认系列",
        "merge_series": "合并系列",
        "target_series_id": "目标系列 ID",
        "rebuild_series": "批量重算系列",
        "thread": "帖子",
        "create_export_job": "创建导出任务",
        "raw_title": "原始标题",
        "publisher": "发布者",
        "group_name": "汉化组",
        "author_guess": "作者名",
        "chapter_title": "章节标题",
        "context_path": "上下文路径",
        "export_path": "导出路径",
        "open_metadata": "打开 metadata.json",
        "open_context": "打开 context.md",
        "metadata_preview": "Metadata 预览",
        "context_preview": "Context 预览",
        "reading_preview": "阅读预览",
        "reading_preview_tip": "这里按楼层顺序合并展示正文和本地归档图片，尽量贴近原帖阅读顺序。",
        "image_width": "图片宽度",
        "image_width_50": "50%",
        "image_width_75": "75%",
        "image_width_100": "100%",
        "back_to_top": "回到顶部",
        "job_submitted": "已创建任务",
        "floors": "楼层",
        "floor_no": "楼层号",
        "pid": "PID",
        "content": "内容",
        "job_detail": "任务详情",
        "tid": "TID",
        "heartbeat": "心跳时间",
        "lease_until": "租约到期",
        "payload": "Payload",
        "artifacts": "Artifacts",
        "staging": "Staging",
        "failure_preview": "失败预览",
        "snapshot_preview": "快照预览",
        "title_parse_log_preview": "标题提取日志预览",
        "events_timeline": "事件时间线",
        "event_type": "事件类型",
        "diagnostics": "排障提示",
        "job_diagnostics_intro": "下面这些文件能帮助你判断任务卡在哪一段，以及数据有没有落干净。",
        "job_hint_snapshot": "存在 snapshot.json：说明页面解析已经成功，问题更可能出在校验、图片下载或数据库写入阶段。",
        "job_hint_failure": "存在 failure.json：优先看 stage、error_type、error_message，可以快速定位失败边界。",
        "job_hint_title_parse_log": "存在 title_parse_log.json：可以对比规则解析 baseline 和 LLM 最终结果，判断标题抽取是否跑偏。",
        "job_hint_partial": "任务为 partial：帖子已归档，但仍有图片缺失或下载失败，需要结合 metadata / missing_image_urls 排查。",
        "schema_version": "Schema 版本",
        "thread_count": "帖子数",
        "series_count": "系列数",
        "audit_event_count": "审计事件数",
        "view_thread": "查看帖子",
        "quick_actions": "快捷操作",
        "remote_url": "远端帖子 URL",
        "base_url": "站点根 URL",
        "sync_thread_now": "创建同步任务",
        "resync_thread": "重新同步",
        "force_resync_export": "重新同步并导出",
        "export_strategy": "导出策略",
        "cache_only": "仅本地缓存",
        "sync_if_stale": "过旧则同步",
        "force_resync": "强制重同步",
        "open_jobs": "查看任务",
        "open_threads": "查看帖子",
        "operations": "操作",
        "delete_thread": "删除归档",
        "delete_series": "删除系列",
        "cannot_delete_nonempty_series": "仅允许删除没有关联帖子的空系列。",
        "deleted": "已删除",
        "compact_hint": "已切换为紧凑列表，单页显示更多记录。",
        "thread_list_hint": "按标题、系列、章节与状态分组显示，减少横向滚动。",
        "archive_meta": "归档信息",
        "title_meta": "标题信息",
        "status_meta": "状态",
        "open_thread": "打开详情",
        "job_list_hint": "按任务类型、状态、阶段与报错分组显示，便于快速定位异常。",
        "series_list_hint": "按系列标题、作者、归并状态与操作分组显示，减少横向滚动。",
        "export_list_hint": "按导出目标、归档状态与文件位置分组显示，方便批量检查。",
        "job_meta": "任务信息",
        "error_meta": "错误与时间",
        "export_meta": "导出信息",
        "open_job": "打开任务",
        "open_series": "打开系列",
        "dashboard_jobs_hint": "最近任务按类型、状态和错误分组显示，方便快速发现失败点。",
        "dashboard_workers_hint": "Worker 心跳按实例聚合展示，便于判断是否仍在稳定消费任务。",
        "dashboard_audit_hint": "最近审计事件显示关键数据变更，适合追踪人工操作。",
        "dashboard_threads_hint": "最近归档漫画沿用帖子卡片视图，便于直接继续排查或导出。",
        "review_series_hint": "系列复核按标题、作者、别名与操作分组显示，和帖子复核保持一致。",
        "review_meta": "复核状态",
        "audit_meta": "审计信息",
        "overview": "概览",
        "archive_ready": "归档完成",
        "archive_partial": "归档部分完成",
        "archive_failed": "归档失败",
        "start_sync": "发起同步",
        "thread_not_found": "帖子不存在",
        "images": "图片预览",
        "no_images": "当前帖子没有本地归档图片可预览。",
        "open_image": "打开原图",
        "image_preview_tip": "这里展示的是本地归档图片，优先用于核验漫画内容是否保存完整。",
        "recent_archived_threads": "最近归档漫画",
        "no_archived_threads": "当前还没有可展示的归档漫画。",
        "no_review_items": "当前没有待复核的标题。",
        "subtitle": "补充信息",
        "tags": "标签",
        "chapter_index": "章节序号",
        "chapter_index_end": "章节序号（结束）",
        "one_per_line": "每行一个",
    },
    "en": {
        "dashboard": "Dashboard",
        "jobs": "Jobs",
        "threads": "Threads",
        "series": "Series",
        "title_review": "Title Review",
        "exports": "Exports",
        "language": "Language",
        "switch_zh": "Chinese",
        "switch_en": "English",
        "data_dir": "Data dir",
        "create_noop_job": "Create no-op job",
        "local_html_path": "Local HTML path",
        "create_sync_job": "Create sync job",
        "recent_jobs": "Recent Jobs",
        "worker_heartbeats": "Worker Heartbeats",
        "recent_audit_events": "Recent Audit Events",
        "id": "ID",
        "type": "Type",
        "status": "Status",
        "stage": "Stage",
        "error": "Error",
        "updated": "Updated",
        "worker": "Worker",
        "latest_heartbeat": "Latest heartbeat",
        "latest_update": "Latest update",
        "running_jobs": "Running jobs",
        "seen_jobs": "Seen jobs",
        "action": "Action",
        "target": "Target",
        "actor": "Actor",
        "at": "At",
        "status_filters": "Status filters",
        "all": "All",
        "queued": "Queued",
        "running": "Running",
        "succeeded": "Succeeded",
        "failed": "Failed",
        "interrupted": "Interrupted",
        "progress": "Progress",
        "error_code": "Error code",
        "error_message": "Error message",
        "created": "Created",
        "finished": "Finished",
        "created_job": "Created job",
        "search": "Search",
        "title": "Title",
        "core_title": "Core title",
        "chapter": "Chapter",
        "series_key": "Series key",
        "archive": "Archive",
        "validation": "Validation",
        "synced": "Synced",
        "search_placeholder": "Search title, author, group, content",
        "canonical_title": "Canonical title",
        "author": "Author",
        "confidence": "Confidence",
        "needs_review": "Needs review",
        "last_sync": "Last sync",
        "aliases": "Aliases",
        "thread_titles": "Thread Titles",
        "edit_title_fields": "Edit Title Fields",
        "confirm_title": "Confirm title",
        "save_title_review": "Save and confirm",
        "confirm_series": "Confirm series",
        "merge_series": "Merge series",
        "target_series_id": "Target series ID",
        "rebuild_series": "Rebuild series",
        "thread": "Thread",
        "create_export_job": "Create export job",
        "raw_title": "Raw title",
        "publisher": "Publisher",
        "group_name": "Scanlation group",
        "author_guess": "Author",
        "chapter_title": "Chapter title",
        "context_path": "Context path",
        "export_path": "Export path",
        "open_metadata": "Open metadata.json",
        "open_context": "Open context.md",
        "metadata_preview": "Metadata Preview",
        "context_preview": "Context Preview",
        "reading_preview": "Reading Preview",
        "reading_preview_tip": "This view merges floor text and archived local images to resemble the original thread reading order.",
        "image_width": "Image width",
        "image_width_50": "50%",
        "image_width_75": "75%",
        "image_width_100": "100%",
        "back_to_top": "Back to top",
        "job_submitted": "Job created",
        "floors": "Floors",
        "floor_no": "Floor",
        "pid": "PID",
        "content": "Content",
        "job_detail": "Job Detail",
        "tid": "TID",
        "heartbeat": "Heartbeat",
        "lease_until": "Lease until",
        "payload": "Payload",
        "artifacts": "Artifacts",
        "staging": "Staging",
        "failure_preview": "Failure Preview",
        "snapshot_preview": "Snapshot Preview",
        "title_parse_log_preview": "Title Parse Log Preview",
        "events_timeline": "Events Timeline",
        "event_type": "Event type",
        "diagnostics": "Diagnostics",
        "job_diagnostics_intro": "Use these artifacts to see where the job stopped and whether data made it through each stage.",
        "job_hint_snapshot": "snapshot.json exists: HTML parsing succeeded, so issues are more likely in validation, image download, or DB commit.",
        "job_hint_failure": "failure.json exists: start with stage, error_type, and error_message to locate the failure boundary.",
        "job_hint_title_parse_log": "title_parse_log.json exists: compare the rule baseline with the LLM result to inspect title extraction drift.",
        "job_hint_partial": "Job status is partial: the thread was archived, but some images are still missing or failed to download.",
        "schema_version": "Schema version",
        "thread_count": "Thread count",
        "series_count": "Series count",
        "audit_event_count": "Audit event count",
        "view_thread": "View thread",
        "quick_actions": "Quick Actions",
        "remote_url": "Remote thread URL",
        "base_url": "Site base URL",
        "sync_thread_now": "Create sync job",
        "resync_thread": "Resync",
        "force_resync_export": "Resync + export",
        "export_strategy": "Export strategy",
        "cache_only": "Cache only",
        "sync_if_stale": "Sync if stale",
        "force_resync": "Force resync",
        "open_jobs": "Open jobs",
        "open_threads": "Open threads",
        "operations": "Actions",
        "delete_thread": "Delete archive",
        "delete_series": "Delete series",
        "cannot_delete_nonempty_series": "Only empty series without linked threads can be deleted.",
        "deleted": "Deleted",
        "compact_hint": "Compact layout enabled to fit more rows on each page.",
        "thread_list_hint": "Grouped by title, series, chapter, and status to reduce horizontal scrolling.",
        "archive_meta": "Archive",
        "title_meta": "Title",
        "status_meta": "Status",
        "open_thread": "Open",
        "job_list_hint": "Grouped by type, status, stage, and errors for quicker scanning.",
        "series_list_hint": "Grouped by title, author, merge state, and actions to reduce horizontal scrolling.",
        "export_list_hint": "Grouped by export target, archive state, and file path for quick checks.",
        "job_meta": "Job",
        "error_meta": "Error & Time",
        "export_meta": "Export",
        "open_job": "Open job",
        "open_series": "Open series",
        "dashboard_jobs_hint": "Recent jobs are grouped by type, status, and errors for quick triage.",
        "dashboard_workers_hint": "Worker heartbeats are grouped by instance to show whether processing is healthy.",
        "dashboard_audit_hint": "Recent audit events highlight key data changes for operator tracing.",
        "dashboard_threads_hint": "Recently archived comics reuse the thread card layout for quick follow-up actions.",
        "review_series_hint": "Series review is grouped by title, author, aliases, and actions to match thread review.",
        "review_meta": "Review",
        "audit_meta": "Audit",
        "overview": "Overview",
        "archive_ready": "Archive ready",
        "archive_partial": "Archive partial",
        "archive_failed": "Archive failed",
        "start_sync": "Start sync",
        "thread_not_found": "Thread not found",
        "images": "Image Preview",
        "no_images": "No archived local images are available for this thread.",
        "open_image": "Open image",
        "image_preview_tip": "These are local archived images, shown first so you can verify comic content integrity.",
        "recent_archived_threads": "Recently Archived Comics",
        "no_archived_threads": "No archived comics are available yet.",
        "no_review_items": "There are no title review items right now.",
        "subtitle": "Subtitle",
        "tags": "Tags",
        "chapter_index": "Chapter index",
        "chapter_index_end": "Chapter index (end)",
        "one_per_line": "One per line",
    },
}


def _job_to_dict(job) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "stage": job.stage,
        "tid": job.tid,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "worker_id": job.worker_id,
        "heartbeat_at": job.heartbeat_at,
        "lease_until": job.lease_until,
        "retry_count": job.retry_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
    }


class WebHandler(BaseHTTPRequestHandler):
    settings: Settings
    lang: str = "zh"

    def _send(self, body: str, status: HTTPStatus = HTTPStatus.OK, content_type: str = "text/html") -> None:
        data = body.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _repo(self) -> tuple[object, JobsRepository]:
        conn = connect(self.settings.db_path)
        migrate(conn)
        return conn, JobsRepository(conn)

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = parse_qs(raw)
        form = {key: values[0] for key, values in parsed.items() if values}
        self.lang = self._resolve_lang(form.get("lang"))
        return form

    def _storage_paths(self) -> StoragePaths:
        return StoragePaths(self.settings.data_dir, export_dir=self.settings.export_dir)

    def _resolve_lang(self, candidate: str | None) -> str:
        return "en" if candidate == "en" else "zh"

    def _activate_lang(self, query: str) -> None:
        params = parse_qs(query)
        self.lang = self._resolve_lang(params.get("lang", [None])[0])

    def _t(self, key: str) -> str:
        return TRANSLATIONS.get(self.lang, {}).get(key) or TRANSLATIONS["zh"].get(key, key)

    def _url(self, path: str) -> str:
        joiner = "&" if "?" in path else "?"
        return f"{path}{joiner}lang={self.lang}"

    def _hidden_lang(self) -> str:
        return f'<input type="hidden" name="lang" value="{self.lang}">'

    def _html_page(self, title: str, body: str, *, auto_refresh_seconds: int | None = None) -> str:
        app_title = "YamiboMCP 漫画归档控制台" if self.lang == "zh" else "YamiboMCP Manga Archive Console"
        nav = (
            f'<div class="topbar"><div><div class="brand">{html.escape(app_title)}</div>'
            f'<div class="subnav"><a href="{self._url("/")}">{self._t("dashboard")}</a>'
            f'<a href="{self._url("/jobs")}">{self._t("jobs")}</a>'
            f'<a href="{self._url("/threads")}">{self._t("threads")}</a>'
            f'<a href="{self._url("/series")}">{self._t("series")}</a>'
            f'<a href="{self._url("/title-review")}">{self._t("title_review")}</a>'
            f'<a href="{self._url("/exports")}">{self._t("exports")}</a></div></div>'
            f'<div class="lang-switch">{self._t("language")}: '
            f'<a href="{self._url_with_lang("zh")}">{self._t("switch_zh")}</a> '
            f'<a href="{self._url_with_lang("en")}">{self._t("switch_en")}</a></div></div>'
        )
        meta_refresh = f'<meta http-equiv="refresh" content="{auto_refresh_seconds}">' if auto_refresh_seconds and auto_refresh_seconds > 0 else ""
        return f"""
        <html><head><title>{html.escape(title)} | {html.escape(app_title)}</title>{meta_refresh}
        <style>
          html {{ scroll-behavior: smooth; }}
          body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f5f7fb; color: #1f2937; }}
          .page {{ width: min(100%, 1680px); margin: 0 auto; padding: 16px 18px 24px; box-sizing: border-box; }}
          .topbar {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 14px; }}
          .brand {{ font-size: 22px; font-weight: 700; }}
          .subnav {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }}
          .subnav a, .lang-switch a {{ color: #1d4ed8; text-decoration: none; }}
          h1 {{ font-size: 20px; margin: 0 0 12px; }}
          h2 {{ font-size: 16px; margin: 18px 0 10px; }}
          .panel {{ background: white; border: 1px solid #dbe3f0; border-radius: 6px; padding: 12px; margin-bottom: 12px; }}
          .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
          .stat {{ background: #fff; border: 1px solid #dbe3f0; border-radius: 6px; padding: 12px; }}
          .stat .label {{ font-size: 12px; color: #64748b; }}
          .stat .value {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
          .table-wrap {{ overflow-x: auto; }}
          table {{ width: 100%; border-collapse: collapse; background: white; }}
          th, td {{ border: 1px solid #dbe3f0; padding: 6px 8px; vertical-align: top; text-align: left; font-size: 13px; line-height: 1.4; }}
          th {{ background: #f8fafc; }}
          table.compact td, table.compact th {{ white-space: nowrap; }}
          .actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
          .actions form {{ margin: 0; }}
          input, select, button, textarea {{ font: inherit; }}
          input[type="text"], input[name="html_path"], input[name="url"], input[name="base_url"], input[name="q"], input[type="number"], textarea {{ padding: 6px 8px; border: 1px solid #cbd5e1; border-radius: 6px; }}
          textarea {{ width: 100%; min-height: 88px; resize: vertical; box-sizing: border-box; }}
          button {{ padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; background: #eff6ff; cursor: pointer; }}
          button.danger {{ background: #fff1f2; border-color: #fecdd3; color: #9f1239; }}
          button:disabled {{ opacity: 0.55; cursor: not-allowed; }}
          .muted {{ color: #64748b; }}
          .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; border: 1px solid #cbd5e1; }}
          pre {{ white-space: pre-wrap; word-break: break-word; margin: 0; }}
          .toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
          .toolbar-label {{ font-size: 12px; color: #64748b; }}
          .segmented {{ display: inline-flex; flex-wrap: wrap; gap: 6px; }}
          .segmented a {{ padding: 5px 10px; border: 1px solid #cbd5e1; border-radius: 6px; background: #fff; color: #1f2937; text-decoration: none; font-size: 12px; }}
          .segmented a.active {{ background: #dbeafe; border-color: #93c5fd; color: #1d4ed8; font-weight: 600; }}
          .hint-list {{ margin: 0; padding-left: 20px; color: #475569; }}
          .hint-list li {{ margin: 6px 0; }}
          .artifact-links {{ display: flex; flex-wrap: wrap; gap: 10px; }}
          .artifact-links a {{ color: #1d4ed8; text-decoration: none; }}
          .image-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
          .image-card {{ background: #fff; border: 1px solid #dbe3f0; border-radius: 6px; padding: 10px; }}
          .image-card img {{ display: block; width: 100%; height: auto; border-radius: 6px; background: #e2e8f0; }}
          .image-card .meta {{ margin-top: 8px; font-size: 12px; color: #64748b; }}
          .reading-flow {{ display: flex; flex-direction: column; gap: 14px; }}
          .floor-block {{ background: #fff; border: 1px solid #dbe3f0; border-radius: 6px; padding: 12px; }}
          .floor-head {{ font-weight: 600; margin-bottom: 10px; }}
          .floor-body {{ white-space: pre-wrap; word-break: break-word; line-height: 1.7; }}
          .floor-images {{ display: flex; flex-direction: column; gap: 12px; margin-top: 12px; }}
          .floor-images a {{ display: block; }}
          .floor-images img {{ display: block; width: min(var(--reading-image-width, 100%), 100%); max-width: 100%; height: auto; border-radius: 6px; background: #e2e8f0; }}
          details.review-card {{ background: #fff; border: 1px solid #dbe3f0; border-radius: 6px; margin-bottom: 14px; overflow: hidden; }}
          details.review-card summary {{ list-style: none; cursor: pointer; padding: 12px 14px; background: #f8fafc; }}
          details.review-card summary::-webkit-details-marker {{ display: none; }}
          .review-meta {{ display: grid; grid-template-columns: minmax(80px, 120px) 1fr; gap: 8px 12px; margin: 0 0 14px; }}
          .review-form {{ padding: 16px; }}
          .review-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
          .field label {{ display: block; font-size: 12px; color: #64748b; margin-bottom: 6px; }}
          .field input[type="text"], .field input[type="number"] {{ width: 100%; box-sizing: border-box; }}
          .field-full {{ grid-column: 1 / -1; }}
          .thread-list {{ display: flex; flex-direction: column; gap: 10px; }}
          .thread-row {{ background: #fff; border: 1px solid #dbe3f0; border-radius: 6px; padding: 12px; display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(220px, 0.9fr) minmax(220px, 1fr); gap: 12px; align-items: start; }}
          .thread-main {{ min-width: 0; }}
          .thread-main-title {{ font-size: 15px; font-weight: 600; line-height: 1.45; margin-bottom: 6px; word-break: break-word; }}
          .thread-main-sub {{ color: #64748b; font-size: 12px; display: flex; flex-wrap: wrap; gap: 6px 10px; }}
          .thread-inline-actions {{ display: inline-flex; flex-wrap: wrap; gap: 6px; margin-left: 8px; vertical-align: middle; }}
          .thread-inline-actions form {{ margin: 0; }}
          .thread-inline-actions button {{ padding: 4px 8px; font-size: 12px; }}
          .thread-meta {{ min-width: 0; }}
          .thread-meta-label {{ font-size: 11px; color: #94a3b8; text-transform: uppercase; margin-bottom: 6px; }}
          .thread-meta-lines {{ display: flex; flex-direction: column; gap: 6px; font-size: 13px; }}
          .thread-meta-lines div {{ word-break: break-word; }}
          .item-list {{ display: flex; flex-direction: column; gap: 10px; }}
          .item-row {{ background: #fff; border: 1px solid #dbe3f0; border-radius: 6px; padding: 12px; display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(220px, 0.9fr) minmax(240px, 1fr); gap: 12px; align-items: start; }}
          .item-main {{ min-width: 0; }}
          .item-main-title {{ font-size: 15px; font-weight: 600; line-height: 1.45; margin-bottom: 6px; word-break: break-word; }}
          .item-main-sub {{ color: #64748b; font-size: 12px; display: flex; flex-wrap: wrap; gap: 6px 10px; }}
          .item-inline-actions {{ display: inline-flex; flex-wrap: wrap; gap: 6px; margin-left: 8px; vertical-align: middle; }}
          .item-inline-actions form {{ margin: 0; }}
          .item-inline-actions button {{ padding: 4px 8px; font-size: 12px; }}
          .item-meta {{ min-width: 0; }}
          .item-meta-label {{ font-size: 11px; color: #94a3b8; text-transform: uppercase; margin-bottom: 6px; }}
          .item-meta-lines {{ display: flex; flex-direction: column; gap: 6px; font-size: 13px; }}
          .item-meta-lines div {{ word-break: break-word; }}
          .back-to-top {{ position: fixed; right: 20px; bottom: 20px; z-index: 20; display: inline-flex; align-items: center; justify-content: center; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 999px; background: rgba(255, 255, 255, 0.96); color: #1f2937; text-decoration: none; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12); font-size: 12px; }}
          .back-to-top:hover {{ background: #eff6ff; color: #1d4ed8; }}
          @media (max-width: 980px) {{
            .thread-row {{ grid-template-columns: 1fr; }}
            .thread-inline-actions {{ display: flex; margin-left: 0; margin-top: 8px; }}
            .item-row {{ grid-template-columns: 1fr; }}
            .item-inline-actions {{ display: flex; margin-left: 0; margin-top: 8px; }}
          }}
        </style></head>
        <body>
          <div id="top"></div>
          <div class="page">
            {nav}
            <h1>{html.escape(title)}</h1>
            {body}
          </div>
          <a class="back-to-top" href="#top">{self._t("back_to_top")}</a>
        </body></html>
        """

    def _read_text(self, path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="ignore")

    def _url_with_lang(self, lang: str) -> str:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        params["lang"] = [lang]
        query = urlencode(params, doseq=True)
        return parsed.path if not query else f"{parsed.path}?{query}"

    def _thread_detail_width(self) -> int:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        raw = params.get("img_width", ["75"])[0]
        return int(raw) if raw in {"50", "75", "100"} else 75

    def _thread_detail_url(self, tid: int, *, img_width: int, job_id: str | None = None) -> str:
        payload = {"lang": self.lang, "img_width": str(img_width)}
        if job_id:
            payload["job"] = job_id
        query = urlencode(payload)
        return f"/threads/{tid}?{query}"

    def _current_query_params(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _is_active_job_status(self, status: str | None) -> bool:
        return status in {"queued", "running", "retrying"}

    def _thread_active_job(self, repo: JobsRepository, tid: int) -> object | None:
        params = self._current_query_params()
        job_id = params.get("job", [""])[0].strip()
        if job_id:
            try:
                job = repo.get(job_id)
            except Exception:
                job = None
            if job is not None and job.tid == tid and self._is_active_job_status(job.status):
                return job
        for job in repo.list(limit=50):
            if job.tid == tid and self._is_active_job_status(job.status):
                return job
        return None

    def _thread_image_width_toolbar(self, tid: int, *, img_width: int) -> str:
        options = [50, 75, 100]
        links = "".join(
            f'<a class="{"active" if option == img_width else ""}" href="{self._thread_detail_url(tid, img_width=option)}">{self._t(f"image_width_{option}")}</a>'
            for option in options
        )
        return (
            f'<div class="toolbar">'
            f'<span class="toolbar-label">{self._t("image_width")}:</span>'
            f'<div class="segmented">{links}</div>'
            f"</div>"
        )

    def _preview(self, value: str | None, *, limit: int = 4000) -> str:
        if not value:
            return ""
        if len(value) <= limit:
            return value
        # 预览只截前面一段，页面里保留原始文件链接继续查看。
        return value[:limit] + "\n...<truncated>..."

    def _json_block(self, value: object) -> str:
        return f"<pre>{html.escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre>"

    def _clean_optional_text(self, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None

    def _parse_multiline_list(self, value: str | None) -> list[str]:
        if not value:
            return []
        return [item.strip() for item in value.replace("\r", "\n").split("\n") if item.strip()]

    def _parse_optional_float(self, value: str | None) -> float | None:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError as exc:
            raise ValueError(f"invalid number: {cleaned}") from exc

    def _json_list_to_lines(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return value
        if not isinstance(loaded, list):
            return ""
        return "\n".join(str(item).strip() for item in loaded if str(item).strip())

    def _worker_heartbeats(self, conn) -> list[dict[str, object]]:
        rows = conn.execute(
            """
            SELECT
              worker_id,
              MAX(heartbeat_at) AS latest_heartbeat_at,
              MAX(updated_at) AS latest_updated_at,
              SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_jobs,
              COUNT(*) AS seen_jobs
            FROM jobs
            WHERE worker_id IS NOT NULL
            GROUP BY worker_id
            ORDER BY COALESCE(MAX(heartbeat_at), MAX(updated_at)) DESC
            LIMIT 20
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def _render_title_review_card(self, row) -> str:
        alias_text = self._json_list_to_lines(row["title_aliases_json"])
        tags_text = self._json_list_to_lines(row["tags_json"])
        summary_title = html.escape(row["display_title"] or row["raw_title"] or "")
        return f"""
        <details class="review-card">
          <summary>
            <div><strong><a href="{self._url(f"/threads/{row["tid"]}")}">{row["tid"]}</a></strong> · {summary_title}</div>
            <div class="muted">{html.escape(row["core_title_guess"] or '')} | {html.escape(row["author_guess"] or '')} | {html.escape(row["series_key"] or '')} | {self._t("confidence")}: {row["confidence"]}</div>
          </summary>
          <div class="review-form">
            <div class="review-meta">
              <div class="muted">{self._t("raw_title")}</div><div>{html.escape(row["raw_title"] or "")}</div>
              <div class="muted">{self._t("tid")}</div><div>{row["tid"]}</div>
            </div>
            <form method="post" action="{self._url("/title-review/update-title")}">
              {self._hidden_lang()}
              <input type="hidden" name="tid" value="{row["tid"]}">
              <div class="review-grid">
                <div class="field field-full"><label>{self._t("title")}</label><input type="text" name="display_title" value="{html.escape(row["display_title"] or row["raw_title"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("group_name")}</label><input type="text" name="group_name" value="{html.escape(row["group_name"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("author_guess")}</label><input type="text" name="author_guess" value="{html.escape(row["author_guess"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("core_title")}</label><input type="text" name="core_title_guess" value="{html.escape(row["core_title_guess"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("series_key")}</label><input type="text" name="series_key" value="{html.escape(row["series_key"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("chapter")}</label><input type="text" name="chapter_name" value="{html.escape(row["chapter_name"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("chapter_index")}</label><input type="number" step="0.01" name="chapter_index" value="{'' if row["chapter_index"] is None else row["chapter_index"]}"></div>
                <div class="field"><label>{self._t("chapter_index_end")}</label><input type="number" step="0.01" name="chapter_index_end" value="{'' if row.get("chapter_index_end") is None else row["chapter_index_end"]}"></div>
                <div class="field"><label>{self._t("chapter_title")}</label><input type="text" name="chapter_title" value="{html.escape(row["chapter_title"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("confidence")}</label><input type="number" step="0.01" min="0" max="1" name="confidence" value="{'' if row["confidence"] is None else row["confidence"]}"></div>
                <div class="field field-full"><label>{self._t("subtitle")}</label><input type="text" name="subtitle" value="{html.escape(row["subtitle"] or "", quote=True)}"></div>
                <div class="field"><label>{self._t("aliases")} ({self._t("one_per_line")})</label><textarea name="title_aliases">{html.escape(alias_text)}</textarea></div>
                <div class="field"><label>{self._t("tags")} ({self._t("one_per_line")})</label><textarea name="tags">{html.escape(tags_text)}</textarea></div>
              </div>
              <div class="actions" style="margin-top:12px;">
                <button type="submit">{self._t("save_title_review")}</button>
              </div>
            </form>
            <div class="actions" style="margin-top:10px;">
              <form method="post" action="{self._url("/title-review/confirm-title")}">
                {self._hidden_lang()}
                <input type="hidden" name="tid" value="{row["tid"]}">
                <button type="submit">{self._t("confirm_title")}</button>
              </form>
            </div>
          </div>
        </details>
        """

    def _job_artifact_path(self, job_id: str, filename: str) -> Path:
        return self._storage_paths().staging_job_dir(job_id) / filename

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        self._activate_lang(parsed.query)
        if parsed.path == "/jobs":
            self._jobs(parsed.query)
        elif parsed.path.startswith("/jobs/"):
            self._job_detail(parsed.path)
        elif parsed.path == "/threads":
            self._threads(parsed.query)
        elif parsed.path.startswith("/threads/"):
            self._thread_detail(parsed.path)
        elif parsed.path.startswith("/media/"):
            self._media_file(parsed.path)
        elif parsed.path.startswith("/artifacts/jobs/"):
            self._job_artifact(parsed.path)
        elif parsed.path == "/series":
            self._series()
        elif parsed.path.startswith("/series/"):
            self._series_detail(parsed.path)
        elif parsed.path == "/title-review":
            self._title_review()
        elif parsed.path == "/exports":
            self._exports()
        elif parsed.path == "/":
            self._dashboard()
        else:
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        self._activate_lang(parsed.query)
        if parsed.path == "/jobs/noop":
            self._create_noop()
        elif parsed.path == "/jobs/sync-thread":
            self._create_sync_thread()
        elif parsed.path == "/jobs/resync-thread":
            self._create_resync_thread()
        elif parsed.path == "/threads/delete":
            self._delete_thread()
        elif parsed.path == "/series/delete":
            self._delete_series()
        elif parsed.path == "/title-review/confirm-title":
            self._confirm_title_review()
        elif parsed.path == "/title-review/update-title":
            self._update_title_review()
        elif parsed.path == "/title-review/confirm-series":
            self._confirm_series_review()
        elif parsed.path == "/title-review/merge-series":
            self._merge_series_review()
        elif parsed.path == "/jobs/rebuild-series":
            self._create_rebuild_series_job()
        elif parsed.path == "/jobs/export-thread":
            self._create_export_thread()
        elif parsed.path == "/jobs/reexport-thread":
            self._create_reexport_thread()
        else:
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")

    def _dashboard(self) -> None:
        conn, repo = self._repo()
        try:
            recent_jobs = repo.list(limit=8)
            recent_audits = AuditEventsRepository(conn).list_recent(limit=8)
            worker_rows = self._worker_heartbeats(conn)
            archived_threads = ThreadsRepository(conn).list_threads(limit=20)
            thread_count = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            series_count = conn.execute("SELECT COUNT(*) FROM series").fetchone()[0]
            finished_exports = conn.execute("SELECT COUNT(*) FROM threads WHERE is_exported = 1").fetchone()[0]
            recent_job_rows = "\n".join(self._render_job_list_row(job) for job in recent_jobs)
            worker_cards = "\n".join(self._render_worker_list_row(row) for row in worker_rows)
            audit_cards = "\n".join(self._render_audit_list_row(row) for row in recent_audits)
            archived_cards = "\n".join(self._render_thread_list_row(row) for row in archived_threads)
            body = f"""
          <div class="grid">
            <div class="stat"><div class="label">{self._t("thread_count")}</div><div class="value">{thread_count}</div></div>
            <div class="stat"><div class="label">{self._t("series_count")}</div><div class="value">{series_count}</div></div>
            <div class="stat"><div class="label">{self._t("exports")}</div><div class="value">{finished_exports}</div></div>
            <div class="stat"><div class="label">{self._t("data_dir")}</div><div class="value" style="font-size:14px">{html.escape(str(self.settings.data_dir))}</div></div>
          </div>
          <h2>{self._t("recent_jobs")}</h2>
          <p class="muted">{self._t("dashboard_jobs_hint")}</p>
          <div class="item-list">{recent_job_rows}</div>
          <h2>{self._t("worker_heartbeats")}</h2>
          <p class="muted">{self._t("dashboard_workers_hint")}</p>
          <div class="item-list">{worker_cards}</div>
          <h2>{self._t("recent_audit_events")}</h2>
          <p class="muted">{self._t("dashboard_audit_hint")}</p>
          <div class="item-list">{audit_cards}</div>
          <h2>{self._t("recent_archived_threads")}</h2>
          <p class="muted">{self._t("dashboard_threads_hint")}</p>
          <div class="thread-list">{archived_cards or f'<div class="panel muted">{self._t("no_archived_threads")}</div>'}</div>
            """
            self._send(self._html_page("YamiboMCP", body))
        finally:
            conn.close()

    def _render_worker_list_row(self, row: dict[str, object]) -> str:
        return f"""
          <div class="item-row">
            <div class="item-main">
              <div class="item-main-title">{html.escape(str(row['worker_id'] or ''))}</div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("status_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("running_jobs")}:</strong> {row['running_jobs']}</div>
                <div><strong>{self._t("seen_jobs")}:</strong> {row['seen_jobs']}</div>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("updated")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("latest_heartbeat")}:</strong> {html.escape(str(row['latest_heartbeat_at'] or '-'))}</div>
                <div><strong>{self._t("latest_update")}:</strong> {html.escape(str(row['latest_updated_at'] or '-'))}</div>
              </div>
            </div>
          </div>
        """

    def _render_audit_list_row(self, row: dict[str, object]) -> str:
        return f"""
          <div class="item-row">
            <div class="item-main">
              <div class="item-main-title">{html.escape(row['action'])}</div>
              <div class="item-main-sub">
                <span><strong>{self._t("actor")}:</strong> {html.escape(row['actor'])}</span>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("audit_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("target")}:</strong> {html.escape(row['target_type'])}:{html.escape(row['target_id'])}</div>
                <div><strong>{self._t("at")}:</strong> {html.escape(row['created_at'])}</div>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("status_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>ID:</strong> {html.escape(str(row['event_id']))}</div>
              </div>
            </div>
          </div>
        """

    def _jobs(self, query: str) -> None:
        params = parse_qs(query)
        status = params.get("status", [None])[0]
        created = params.get("created", [""])[0].strip()
        conn, repo = self._repo()
        try:
            jobs = repo.list(limit=150, status=status)
            auto_refresh = 3 if any(self._is_active_job_status(job.status) for job in jobs) else None
            status_links = " | ".join(
                f'<a href="{self._url("/jobs" if value is None else f"/jobs?status={value}")}">{self._t(label.lower()) if label != "All" else self._t("all")}</a>'
                for value, label in (
                    (None, "All"),
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                    ("interrupted", "Interrupted"),
                )
            )
            rows = "\n".join(self._render_job_list_row(job) for job in jobs)
            created_notice = ""
            if created:
                created_notice = f"<p><strong>{self._t('created_job')}:</strong> <a href=\"{self._url(f'/jobs/{html.escape(created)}')}\">{html.escape(created)}</a></p>"
            body = f"""
              {created_notice}
              <p>{self._t("status_filters")}: {status_links}</p>
              <form method="post" action="{self._url("/jobs/noop")}">
                {self._hidden_lang()}
                <button type="submit">{self._t("create_noop_job")}</button>
              </form>
              <form method="post" action="{self._url("/jobs/sync-thread")}">
                {self._hidden_lang()}
                <div class="toolbar">
                  <label>{self._t("local_html_path")}
                    <input name="html_path" size="36" placeholder="html_sample/thread.html">
                  </label>
                  <label>TID <input name="tid" size="10" placeholder="572272"></label>
                  <label>{self._t("remote_url")} <input name="url" size="36" placeholder="https://bbs.yamibo.com/thread-572272-1-1.html"></label>
                  <button type="submit">{self._t("sync_thread_now")}</button>
                </div>
              </form>
              <p class="muted">{self._t("job_list_hint")}</p>
              <div class="item-list">{rows}</div>
            """
            self._send(self._html_page(self._t("jobs"), body, auto_refresh_seconds=auto_refresh))
        finally:
            conn.close()

    def _render_job_list_row(self, job: object) -> str:
        progress = f"{job.progress_current}/{job.progress_total or '?'}"
        error_code = html.escape(job.error_code or "")
        error_message = html.escape(job.error_message or "")
        return f"""
          <div class="item-row">
            <div class="item-main">
              <div class="item-main-title"><a href="{self._url(f"/jobs/{job.job_id}")}">{html.escape(job.job_id)}</a></div>
              <div class="item-main-sub">
                <span><strong>{self._t("type")}:</strong> {html.escape(job.job_type)}</span>
                <span><strong>{self._t("worker")}:</strong> {html.escape(job.worker_id or "-")}</span>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("job_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("status")}:</strong> {html.escape(job.status)}</div>
                <div><strong>{self._t("stage")}:</strong> {html.escape(job.stage or "-")}</div>
                <div><strong>{self._t("progress")}:</strong> {progress}</div>
                <div><a href="{self._url(f"/jobs/{job.job_id}")}">{self._t("open_job")}</a></div>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("error_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("error_code")}:</strong> {error_code or "-"}</div>
                <div><strong>{self._t("error_message")}:</strong> {error_message or "-"}</div>
                <div><strong>{self._t("created")}:</strong> {html.escape(job.created_at)}</div>
                <div><strong>{self._t("finished")}:</strong> {html.escape(job.finished_at or "-")}</div>
              </div>
            </div>
          </div>
        """

    def _job_detail(self, path: str) -> None:
        job_id = path.removeprefix("/jobs/").strip("/")
        if not job_id:
            self._send("job id is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        conn, repo = self._repo()
        try:
            job = repo.get(job_id)
            events = JobEventsRepository(conn).list(job_id=job_id, limit=200)
            auto_refresh = 3 if self._is_active_job_status(job.status) else None
            snapshot_text = self._read_text(self._job_artifact_path(job_id, "snapshot.json"))
            failure_text = self._read_text(self._job_artifact_path(job_id, "failure.json"))
            title_parse_log_text = self._read_text(self._job_artifact_path(job_id, "title_parse_log.json"))
            snapshot_link = f'<a href="{self._url(f"/artifacts/jobs/{html.escape(job_id)}/snapshot.json")}">snapshot.json</a>' if snapshot_text else ""
            failure_link = f'<a href="{self._url(f"/artifacts/jobs/{html.escape(job_id)}/failure.json")}">failure.json</a>' if failure_text else ""
            title_parse_log_link = f'<a href="{self._url(f"/artifacts/jobs/{html.escape(job_id)}/title_parse_log.json")}">title_parse_log.json</a>' if title_parse_log_text else ""
            diagnostics = self._job_diagnostics(job.status, snapshot_text, failure_text, title_parse_log_text)
            body = f"""
              <div class="panel">
                <h2>{self._t("diagnostics")}</h2>
                <p class="muted">{self._t("job_diagnostics_intro")}</p>
                {diagnostics}
              </div>
              <table border="1" cellspacing="0" cellpadding="4">
                <tr><th>{self._t("id")}</th><td>{html.escape(job.job_id)}</td></tr>
                <tr><th>{self._t("type")}</th><td>{html.escape(job.job_type)}</td></tr>
                <tr><th>{self._t("status")}</th><td>{html.escape(job.status)}</td></tr>
                <tr><th>{self._t("stage")}</th><td>{html.escape(job.stage or '')}</td></tr>
                <tr><th>{self._t("tid")}</th><td>{'' if job.tid is None else job.tid}</td></tr>
                <tr><th>{self._t("progress")}</th><td>{job.progress_current}/{job.progress_total or '?'}</td></tr>
                <tr><th>{self._t("worker")}</th><td>{html.escape(job.worker_id or '')}</td></tr>
                <tr><th>{self._t("heartbeat")}</th><td>{html.escape(job.heartbeat_at or '')}</td></tr>
                <tr><th>{self._t("lease_until")}</th><td>{html.escape(job.lease_until or '')}</td></tr>
                <tr><th>{self._t("error")}</th><td>{html.escape(job.error_code or '')} {html.escape(job.error_message or '')}</td></tr>
                <tr><th>{self._t("created")}</th><td>{html.escape(job.created_at)}</td></tr>
                <tr><th>{self._t("updated")}</th><td>{html.escape(job.updated_at)}</td></tr>
                <tr><th>{self._t("finished")}</th><td>{html.escape(job.finished_at or '')}</td></tr>
                <tr><th>{self._t("payload")}</th><td>{self._json_block(job.payload)}</td></tr>
                <tr><th>{self._t("artifacts")}</th><td>{self._json_block(job.artifacts)}</td></tr>
                <tr><th>{self._t("staging")}</th><td><div class="artifact-links">{snapshot_link}{failure_link}{title_parse_log_link}</div></td></tr>
              </table>
              <h2>{self._t("events_timeline")}</h2>
              {self._render_job_events_timeline(events)}
              <h2>{self._t("failure_preview")}</h2>
              <pre>{html.escape(self._preview(failure_text))}</pre>
              <h2>{self._t("snapshot_preview")}</h2>
              <pre>{html.escape(self._preview(snapshot_text))}</pre>
              <h2>{self._t("title_parse_log_preview")}</h2>
              <pre>{html.escape(self._preview(title_parse_log_text))}</pre>
            """
            self._send(self._html_page(f"{self._t('job_detail')} {job_id}", body, auto_refresh_seconds=auto_refresh))
        finally:
            conn.close()

    def _render_job_events_timeline(self, events: list[object]) -> str:
        if not events:
            return f"<p class=\"muted\">{html.escape(self._t('all'))}: 0</p>"
        rows = []
        for event in events:
            payload = getattr(event, "payload", {}) or {}
            rows.append(
                "<tr>"
                f"<td>{getattr(event, 'event_id', '')}</td>"
                f"<td>{html.escape(getattr(event, 'created_at', '') or '')}</td>"
                f"<td>{html.escape(getattr(event, 'event_type', '') or '')}</td>"
                f"<td>{html.escape(getattr(event, 'status', '') or '-')}</td>"
                f"<td>{html.escape(getattr(event, 'stage', '') or '-')}</td>"
                f"<td>{self._json_block(payload)}</td>"
                "</tr>"
            )
        return (
            "<table border=\"1\" cellspacing=\"0\" cellpadding=\"4\">"
            f"<tr><th>ID</th><th>{html.escape(self._t('created'))}</th>"
            f"<th>{html.escape(self._t('event_type'))}</th>"
            f"<th>{html.escape(self._t('status'))}</th>"
            f"<th>{html.escape(self._t('stage'))}</th>"
            f"<th>{html.escape(self._t('payload'))}</th></tr>"
            f"{''.join(rows)}"
            "</table>"
        )

    def _job_artifact(self, path: str) -> None:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 4:
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")
            return
        _, _, job_id, filename = parts
        if filename not in {"snapshot.json", "failure.json", "title_parse_log.json"}:
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")
            return
        artifact_path = self._job_artifact_path(job_id, filename)
        content = self._read_text(artifact_path)
        if content is None:
            self._send("Artifact not found", HTTPStatus.NOT_FOUND, "text/plain")
            return
        self._send(content, content_type="application/json")

    def _threads(self, query: str) -> None:
        params = parse_qs(query)
        q = params.get("q", [""])[0].strip()
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            repo = ThreadsRepository(conn)
            threads = repo.search_threads(q, limit=200) if q else repo.list_threads(limit=200)
            rows = "\n".join(self._render_thread_list_row(row) for row in threads)
            body = f"""
              <form method="get" action="/threads">
                {self._hidden_lang()}
                <input name="q" size="50" value="{html.escape(q)}" placeholder="{html.escape(self._t("search_placeholder"))}">
                <button type="submit">{self._t("search")}</button>
              </form>
              <p class="muted">{self._t("thread_list_hint")}</p>
              <div class="thread-list">{rows}</div>
            """
            self._send(self._html_page(self._t("threads"), body))
        finally:
            conn.close()

    def _render_thread_list_row(self, row) -> str:
        tid = int(row["tid"])
        title = html.escape(row["display_title"] or row["raw_title"] or "")
        core_title = html.escape(row["core_title_guess"] or "")
        chapter = html.escape(row["chapter_name"] or "")
        publisher = html.escape(row["publisher"] or "")
        archive_status = html.escape(row["archive_status"] or "")
        validation_status = html.escape(row["validation_status"] or "")
        sync_time = html.escape(row["sync_time"] or "")
        series_cell = self._series_cell(row)
        return f"""
          <div class="thread-row">
            <div class="thread-main">
              <div class="thread-main-title">
                <a href="{self._url(f"/threads/{tid}")}">{title}</a>
                {self._thread_action_forms(tid, compact=True)}
              </div>
              <div class="thread-main-sub">
                <span><strong>{self._t("tid")}:</strong> {tid}</span>
                <span><strong>{self._t("publisher")}:</strong> {publisher or "-"}</span>
              </div>
            </div>
            <div class="thread-meta">
              <div class="thread-meta-label">{self._t("title_meta")}</div>
              <div class="thread-meta-lines">
                <div><strong>{self._t("core_title")}:</strong> {core_title or "-"}</div>
                <div><strong>{self._t("chapter")}:</strong> {chapter or "-"}</div>
                <div><strong>{self._t("series_key")}:</strong> {series_cell or "-"}</div>
              </div>
            </div>
            <div class="thread-meta">
              <div class="thread-meta-label">{self._t("status_meta")}</div>
              <div class="thread-meta-lines">
                <div><strong>{self._t("archive")}:</strong> {archive_status or "-"}</div>
                <div><strong>{self._t("validation")}:</strong> {validation_status or "-"}</div>
                <div><strong>{self._t("synced")}:</strong> {sync_time or "-"}</div>
                <div><a href="{self._url(f"/threads/{tid}")}">{self._t("open_thread")}</a></div>
              </div>
            </div>
          </div>
        """

    def _series_cell(self, row) -> str:
        series_key = html.escape(row["series_key"] or "")
        series_id = row["series_id"]
        if series_id is None:
            return series_key
        return f'<a href="{self._url(f"/series/{series_id}")}">{series_key}</a>'

    def _thread_action_forms(self, tid: int, *, compact: bool = False) -> str:
        action_class = "thread-inline-actions" if compact else "actions"
        return (
            f'<div class="{action_class}">'
            f'<form method="post" action="{self._url("/jobs/resync-thread")}">{self._hidden_lang()}'
            f'<input type="hidden" name="tid" value="{tid}"><button type="submit">{self._t("resync_thread")}</button></form>'
            f'<form method="post" action="{self._url("/jobs/export-thread")}">{self._hidden_lang()}'
            f'<input type="hidden" name="tid" value="{tid}"><input type="hidden" name="strategy" value="sync_if_stale">'
            f'<button type="submit">{self._t("create_export_job")}</button></form>'
            f'<form method="post" action="{self._url("/threads/delete")}">{self._hidden_lang()}'
            f'<input type="hidden" name="tid" value="{tid}"><button class="danger" type="submit">{self._t("delete_thread")}</button></form>'
            f"</div>"
        )

    def _series_action_forms(self, series_id: int, thread_count: int, *, compact: bool = False, allow_merge: bool = False) -> str:
        disabled = ' disabled title="' + html.escape(self._t("cannot_delete_nonempty_series"), quote=True) + '"' if thread_count > 0 else ""
        action_class = "item-inline-actions" if compact else "actions"
        parts = [f'<div class="{action_class}">']
        if allow_merge:
            parts.append(
                f'<form method="post" action="{self._url("/title-review/merge-series")}">{self._hidden_lang()}'
                f'<input type="hidden" name="source_series_id" value="{series_id}">'
                f'<input type="hidden" name="return_to" value="series_detail">'
                f'<input type="text" name="target_series_id" placeholder="{self._t("target_series_id")}" size="10">'
                f'<button type="submit">{self._t("merge_series")}</button></form>'
            )
        parts.append(
            f'<form method="post" action="{self._url("/series/delete")}">{self._hidden_lang()}'
            f'<input type="hidden" name="series_id" value="{series_id}"><button class="danger" type="submit"{disabled}>{self._t("delete_series")}</button></form>'
        )
        parts.append("</div>")
        return "".join(parts)

    def _series(self) -> None:
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            repo = SeriesRepository(conn)
            series_rows = repo.list_series(limit=200)
            rows = "\n".join(self._render_series_list_row(row) for row in series_rows)
            body = f"""
              <p class="muted">{self._t("series_list_hint")}</p>
              <div class="item-list">{rows}</div>
            """
            self._send(self._html_page(self._t("series"), body))
        finally:
            conn.close()

    def _render_series_list_row(self, row) -> str:
        series_id = int(row["series_id"])
        thread_count = int(row["thread_count"] or 0)
        return f"""
          <div class="item-row">
            <div class="item-main">
              <div class="item-main-title">
                <a href="{self._url(f"/series/{series_id}")}">{html.escape(row['canonical_title'] or row['series_key'] or '')}</a>
                {self._series_action_forms(series_id, thread_count, compact=True)}
              </div>
              <div class="item-main-sub">
                <span><strong>{self._t("id")}:</strong> {series_id}</span>
                <span><strong>{self._t("author")}:</strong> {html.escape(row['author_guess'] or "-")}</span>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("title_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("series_key")}:</strong> {html.escape(row['series_key'] or "-")}</div>
                <div><strong>{self._t("threads")}:</strong> {thread_count}</div>
                <div><a href="{self._url(f"/series/{series_id}")}">{self._t("open_series")}</a></div>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("status_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("confidence")}:</strong> {row['merge_confidence']}</div>
                <div><strong>{self._t("needs_review")}:</strong> {row['needs_review']}</div>
                <div><strong>{self._t("last_sync")}:</strong> {html.escape(row['last_sync_time'] or "-")}</div>
              </div>
            </div>
          </div>
        """

    def _series_detail(self, path: str) -> None:
        raw_id = path.removeprefix("/series/").strip("/")
        if not raw_id.isdigit():
            self._send("Invalid series id", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        series_id = int(raw_id)
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            repo = SeriesRepository(conn)
            series = repo.get_series(series_id)
            if series is None:
                self._send("Series not found", HTTPStatus.NOT_FOUND, "text/plain")
                return
            threads = repo.list_threads_for_series(series_id)
            thread_rows = "\n".join(
                "<tr>"
                f'<td><a href="{self._url(f"/threads/{row["tid"]}")}">{row["tid"]}</a></td>'
                f"<td>{html.escape(row['display_title'] or row['raw_title'] or '')}</td>"
                f"<td>{html.escape(row['chapter_name'] or '')}</td>"
                f"<td>{'' if row['chapter_index'] is None else row['chapter_index']}</td>"
                f"<td>{html.escape(row['archive_status'] or '')}</td>"
                f"<td>{html.escape(row['sync_time'] or '')}</td>"
                "</tr>"
                for row in threads
            )
            body = f"""
              <div class="actions" style="margin-bottom:12px;">
                {self._series_action_forms(series_id, len(threads), allow_merge=True)}
              </div>
              <table border="1" cellspacing="0" cellpadding="4">
                <tr><th>{self._t("id")}</th><td>{series['series_id']}</td></tr>
                <tr><th>{self._t("series_key")}</th><td>{html.escape(series['series_key'] or '')}</td></tr>
                <tr><th>{self._t("author")}</th><td>{html.escape(series['author_guess'] or '')}</td></tr>
                <tr><th>Creator key</th><td>{html.escape(series['creator_key'] or '')}</td></tr>
                <tr><th>{self._t("aliases")}</th><td><pre>{html.escape(series['aliases_json'] or '[]')}</pre></td></tr>
                <tr><th>Alias keys</th><td><pre>{html.escape(series['alias_keys_json'] or '[]')}</pre></td></tr>
                <tr><th>{self._t("needs_review")}</th><td>{series['needs_review']}</td></tr>
              </table>
              <h2>{self._t("threads")}</h2>
              <div class="table-wrap"><table class="compact" border="1" cellspacing="0" cellpadding="4">
                <tr><th>{self._t("tid")}</th><th>{self._t("title")}</th><th>{self._t("chapter")}</th><th>Index</th><th>{self._t("archive")}</th><th>{self._t("synced")}</th></tr>
                {thread_rows}
              </table></div>
            """
            self._send(self._html_page(series["canonical_title"] or f"Series {series_id}", body))
        finally:
            conn.close()

    def _title_review(self) -> None:
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            threads_repo = ThreadsRepository(conn)
            series_repo = SeriesRepository(conn)
            title_rows = "\n".join(self._render_title_review_card(row) for row in threads_repo.list_title_review_items(limit=100))
            series_rows = "\n".join(self._render_series_review_card(row) for row in series_repo.list_series_review_items(limit=100))
            body = f"""
              <h2>{self._t("thread_titles")}</h2>
              {title_rows or f'<div class="panel muted">{self._t("no_review_items")}</div>'}
              <h2>{self._t("series")}</h2>
              <p class="muted">{self._t("review_series_hint")}</p>
              <form method="post" action="{self._url("/jobs/rebuild-series")}">
                {self._hidden_lang()}
                <button type="submit">{self._t("rebuild_series")}</button>
              </form>
              <div class="item-list">{series_rows}</div>
            """
            self._send(self._html_page(self._t("title_review"), body))
        finally:
            conn.close()

    def _render_series_review_card(self, row) -> str:
        aliases_text = self._json_list_to_lines(row["aliases_json"])
        series_id = row["series_id"]
        return f"""
          <div class="item-row">
            <div class="item-main">
              <div class="item-main-title"><a href="{self._url(f"/series/{series_id}")}">{html.escape(row['canonical_title'] or row['series_key'] or '')}</a></div>
              <div class="item-main-sub">
                <span><strong>{self._t("id")}:</strong> {series_id}</span>
                <span><strong>{self._t("author")}:</strong> {html.escape(row['author_guess'] or "-")}</span>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("review_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("series_key")}:</strong> {html.escape(row['series_key'] or "-")}</div>
                <div><strong>{self._t("threads")}:</strong> {row['thread_count']}</div>
                <div><strong>{self._t("aliases")}:</strong> <pre>{html.escape(aliases_text or "-")}</pre></div>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("action")}</div>
              <div class="item-meta-lines">
                <div>
                  <form method="post" action="{self._url("/title-review/confirm-series")}">
                    {self._hidden_lang()}
                    <input type="hidden" name="series_id" value="{series_id}">
                    <button type="submit">{self._t("confirm_series")}</button>
                  </form>
                </div>
                <div>
                  <form method="post" action="{self._url("/title-review/merge-series")}">
                    {self._hidden_lang()}
                    <input type="hidden" name="source_series_id" value="{series_id}">
                    <input type="text" name="target_series_id" placeholder="{self._t("target_series_id")}" size="10">
                    <button type="submit">{self._t("merge_series")}</button>
                  </form>
                </div>
              </div>
            </div>
          </div>
        """

    def _exports(self) -> None:
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            exports = ThreadsRepository(conn).list_exports(limit=200)
            rows = "\n".join(self._render_export_list_row(row) for row in exports)
            body = f"""
              <p class="muted">{self._t("export_list_hint")}</p>
              <div class="item-list">{rows}</div>
            """
            self._send(self._html_page(self._t("exports"), body))
        finally:
            conn.close()

    def _render_export_list_row(self, row) -> str:
        tid = int(row["tid"])
        return f"""
          <div class="item-row">
            <div class="item-main">
              <div class="item-main-title">
                <a href="{self._url(f"/threads/{tid}")}">{html.escape(row['display_title'] or row['raw_title'] or '')}</a>
                {self._thread_action_forms(tid, compact=True)}
              </div>
              <div class="item-main-sub">
                <span><strong>{self._t("tid")}:</strong> {tid}</span>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("status_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("archive")}:</strong> {html.escape(row['archive_status'] or "-")}</div>
                <div><a href="{self._url(f"/threads/{tid}")}">{self._t("open_thread")}</a></div>
              </div>
            </div>
            <div class="item-meta">
              <div class="item-meta-label">{self._t("export_meta")}</div>
              <div class="item-meta-lines">
                <div><strong>{self._t("export_path")}:</strong> {html.escape(row['export_path'] or "-")}</div>
              </div>
            </div>
          </div>
        """

    def _thread_detail(self, path: str) -> None:
        suffix = path.removeprefix("/threads/").strip("/")
        if suffix.endswith("/context"):
            raw_tid = suffix.removesuffix("/context")
            self._thread_file(raw_tid, "context")
            return
        if suffix.endswith("/metadata"):
            raw_tid = suffix.removesuffix("/metadata")
            self._thread_file(raw_tid, "metadata")
            return
        raw_tid = suffix
        if not raw_tid.isdigit():
            self._send("Invalid tid", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            repo = ThreadsRepository(conn)
            thread = repo.get_thread(tid)
            if thread is None:
                self._send("Thread not found", HTTPStatus.NOT_FOUND, "text/plain")
                return
            image_width = self._thread_detail_width()
            title = repo.get_title_parse(tid)
            floors = repo.list_floors(tid)
            paths = self._storage_paths()
            metadata_text = self._read_text(paths.thread_metadata(tid))
            metadata = self._parse_metadata_json(metadata_text)
            reading_view = self._render_thread_reading_view(tid, floors, metadata, image_width=image_width)
            width_toolbar = self._thread_image_width_toolbar(tid, img_width=image_width)
            floor_rows = "\n".join(
                "<tr>"
                f"<td>{floor['floor_no']}</td>"
                f"<td>{floor['pid']}</td>"
                f"<td>{html.escape(floor['publisher'] or '')}</td>"
                f"<td><pre>{html.escape((floor['content'] or '')[:2000])}</pre></td>"
                "</tr>"
                for floor in floors
            )
            title_rows = ""
            if title is not None:
                title_rows = f"""
                <tr><th>{self._t("core_title")}</th><td>{html.escape(title['core_title_guess'] or '')}</td></tr>
                <tr><th>{self._t("group_name")}</th><td>{html.escape(title['group_name'] or '')}</td></tr>
                <tr><th>{self._t("author_guess")}</th><td>{html.escape(title['author_guess'] or '')}</td></tr>
                <tr><th>{self._t("chapter")}</th><td>{html.escape(title['chapter_name'] or '')}</td></tr>
                <tr><th>{self._t("chapter_title")}</th><td>{html.escape(title['chapter_title'] or '')}</td></tr>
                <tr><th>{self._t("series_key")}</th><td>{html.escape(title['series_key'] or '')}</td></tr>
                <tr><th>{self._t("aliases")}</th><td><pre>{html.escape(title['title_aliases_json'] or '[]')}</pre></td></tr>
                <tr><th>{self._t("confidence")}</th><td>{title['confidence']}</td></tr>
                <tr><th>{self._t("needs_review")}</th><td>{title['needs_review']}</td></tr>
                """
            body = f"""
              <div class="panel">
                <div class="actions">
                  <a href="{self._url("/threads")}">{self._t("open_threads")}</a>
                  <a href="{self._url("/jobs")}">{self._t("open_jobs")}</a>
                </div>
                <h2>{self._t("quick_actions")}</h2>
              <div class="actions">
                  <form method="post" action="{self._url("/jobs/resync-thread")}">
                    {self._hidden_lang()}
                    <input type="hidden" name="tid" value="{thread['tid']}">
                    <button type="submit">{self._t("resync_thread")}</button>
                  </form>
                  <form method="post" action="{self._url("/jobs/export-thread")}">
                    {self._hidden_lang()}
                    <input type="hidden" name="tid" value="{thread['tid']}">
                    <input type="hidden" name="strategy" value="sync_if_stale">
                    <button type="submit">{self._t("create_export_job")}</button>
                  </form>
                  <form method="post" action="{self._url("/jobs/reexport-thread")}">
                    {self._hidden_lang()}
                    <input type="hidden" name="tid" value="{thread['tid']}">
                    <button type="submit">{self._t("force_resync_export")}</button>
                  </form>
                  <form method="post" action="{self._url("/threads/delete")}">
                    {self._hidden_lang()}
                    <input type="hidden" name="tid" value="{thread['tid']}">
                    <button class="danger" type="submit">{self._t("delete_thread")}</button>
                  </form>
                </div>
              </div>
              <table border="1" cellspacing="0" cellpadding="4">
                <tr><th>{self._t("tid")}</th><td>{thread['tid']}</td></tr>
                <tr><th>{self._t("raw_title")}</th><td>{html.escape(thread['raw_title'] or '')}</td></tr>
                <tr><th>{self._t("publisher")}</th><td>{html.escape(thread['publisher'] or '')}</td></tr>
                <tr><th>{self._t("archive")}</th><td>{html.escape(thread['archive_status'] or '')}</td></tr>
                <tr><th>{self._t("validation")}</th><td>{html.escape(thread['validation_status'] or '')}</td></tr>
                <tr><th>{self._t("context_path")}</th><td>{html.escape(thread['context_path'] or '')}</td></tr>
                <tr><th>{self._t("export_path")}</th><td>{html.escape(thread['export_path'] or '')}</td></tr>
                {title_rows}
              </table>
              <h2>{self._t("reading_preview")}</h2>
              <p class="muted">{self._t("reading_preview_tip")}</p>
              {width_toolbar}
              <p><a href="{self._url(f"/threads/{tid}/context")}">{self._t("open_context")}</a></p>
              {reading_view}
              <h2>{self._t("floors")}</h2>
              <table border="1" cellspacing="0" cellpadding="4">
                <tr><th>{self._t("floor_no")}</th><th>{self._t("pid")}</th><th>{self._t("publisher")}</th><th>{self._t("content")}</th></tr>
                {floor_rows}
              </table>
              <h2>{self._t("metadata_preview")}</h2>
              <p><a href="{self._url(f"/threads/{tid}/metadata")}">{self._t("open_metadata")}</a></p>
              <pre>{html.escape(self._preview(metadata_text))}</pre>
            """
            self._send(self._html_page(thread["display_title"] or thread["raw_title"] or f"Thread {tid}", body))
        finally:
            conn.close()

    def _thread_file(self, raw_tid: str, kind: str) -> None:
        if not raw_tid.isdigit():
            self._send("Invalid tid", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        paths = self._storage_paths()
        target = paths.thread_context(tid) if kind == "context" else paths.thread_metadata(tid)
        content = self._read_text(target)
        if content is None:
            self._send("Artifact not found", HTTPStatus.NOT_FOUND, "text/plain")
            return
        content_type = "text/markdown" if kind == "context" else "application/json"
        self._send(content, content_type=content_type)

    def _media_file(self, path: str) -> None:
        raw_rel = path.removeprefix("/media/").strip("/")
        if not raw_rel:
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")
            return
        rel = Path(unquote(raw_rel))
        target = (self.settings.data_dir / rel).resolve()
        data_root = self.settings.data_dir.resolve()
        try:
            target.relative_to(data_root)
        except ValueError:
            self._send("Forbidden", HTTPStatus.FORBIDDEN, "text/plain")
            return
        if not target.exists() or not target.is_file():
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")
            return
        data = target.read_bytes()
        content_type = self._guess_media_content_type(target, data)
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _job_diagnostics(self, status: str, snapshot_text: str | None, failure_text: str | None, title_parse_log_text: str | None) -> str:
        hints: list[str] = []
        if snapshot_text:
            hints.append(self._t("job_hint_snapshot"))
        if failure_text:
            hints.append(self._t("job_hint_failure"))
        if title_parse_log_text:
            hints.append(self._t("job_hint_title_parse_log"))
        if status == "partial":
            hints.append(self._t("job_hint_partial"))
        if not hints:
            return "<p class=\"muted\">No diagnostics available yet.</p>"
        return "<ul class=\"hint-list\">" + "".join(f"<li>{html.escape(item)}</li>" for item in hints) + "</ul>"

    def _parse_metadata_json(self, metadata_text: str | None) -> dict[str, object] | None:
        if not metadata_text:
            return None
        try:
            parsed = json.loads(metadata_text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _render_thread_image_gallery(self, tid: int, metadata: dict[str, object] | None) -> str:
        if not metadata:
            return f'<div class="panel muted">{html.escape(self._t("no_images"))}</div>'
        floors = metadata.get("floors")
        if not isinstance(floors, list):
            return f'<div class="panel muted">{html.escape(self._t("no_images"))}</div>'
        cards: list[str] = []
        for floor in floors:
            if not isinstance(floor, dict):
                continue
            image_urls = self._detail_image_sources(tid, floor)
            if not isinstance(image_urls, list):
                continue
            floor_no = floor.get("floor_no")
            publisher = floor.get("publisher") or ""
            for idx, image_source in enumerate(image_urls, start=1):
                if not isinstance(image_source, str) or not image_source.strip():
                    continue
                image_src = self._detail_image_src(tid, image_source)
                cards.append(
                    f'<div class="image-card">'
                    f'<a href="{image_src}" target="_blank" rel="noreferrer"><img src="{image_src}" loading="lazy" alt="floor {html.escape(str(floor_no or ""))} image {idx}"></a>'
                    f'<div class="meta">F{html.escape(str(floor_no or ""))} · {html.escape(str(publisher))} · <a href="{image_src}" target="_blank" rel="noreferrer">{html.escape(self._t("open_image"))}</a></div>'
                    f"</div>"
                )
        if not cards:
            return f'<div class="panel muted">{html.escape(self._t("no_images"))}</div>'
        return '<div class="image-grid">' + "".join(cards) + "</div>"

    def _render_thread_reading_view(self, tid: int, floors, metadata: dict[str, object] | None, *, image_width: int) -> str:
        floor_meta_map: dict[int, dict[str, object]] = {}
        if metadata and isinstance(metadata.get("floors"), list):
            for row in metadata["floors"]:
                if isinstance(row, dict):
                    try:
                        floor_meta_map[int(row.get("pid"))] = row
                    except (TypeError, ValueError):
                        continue
        blocks: list[str] = []
        for floor in floors:
            head = f"{floor['floor_no']}F · {html.escape(floor['publisher'] or '')}"
            if floor["pub_time"]:
                head += f" · {html.escape(floor['pub_time'])}"
            body = html.escape(floor["content"] or "")
            floor_meta = floor_meta_map.get(int(floor["pid"]), {})
            image_urls = self._detail_image_sources(tid, floor_meta if isinstance(floor_meta, dict) else None)
            image_html = ""
            if isinstance(image_urls, list) and image_urls:
                rendered = []
                for idx, image_source in enumerate(image_urls, start=1):
                    if not isinstance(image_source, str) or not image_source.strip():
                        continue
                    image_src = self._detail_image_src(tid, image_source)
                    rendered.append(
                        f'<a href="{image_src}" target="_blank" rel="noreferrer" style="--reading-image-width: {image_width}%;">'
                        f'<img src="{image_src}" loading="lazy" alt="floor {floor["floor_no"]} image {idx}"></a>'
                    )
                if rendered:
                    image_html = '<div class="floor-images">' + "".join(rendered) + "</div>"
            blocks.append(
                f'<div class="floor-block">'
                f'<div class="floor-head">{head}</div>'
                f'<div class="floor-body">{body}</div>'
                f"{image_html}"
                f"</div>"
            )
        if not blocks:
            return f'<div class="panel muted">{html.escape(self._t("thread_not_found"))}</div>'
        return '<div class="reading-flow">' + "".join(blocks) + "</div>"

    def _media_relpath(self, tid: int, image_rel: str) -> str:
        normalized = image_rel.strip().lstrip("/")
        if normalized.startswith("threads/") or normalized.startswith("shared/"):
            return normalized
        return f"threads/{tid}/{normalized}"

    def _detail_image_sources(self, tid: int, floor_meta: dict[str, object] | None) -> list[str]:
        if not floor_meta:
            return []
        merged: list[str] = []
        seen: set[str] = set()
        for key in ("image_urls", "shared_image_urls", "skipped_image_urls"):
            value = floor_meta.get(key)
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, str):
                    continue
                cleaned = item.strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                merged.append(cleaned)
        return merged

    def _detail_image_src(self, tid: int, image_source: str) -> str:
        cleaned = image_source.strip()
        if cleaned.startswith(("http://", "https://", "file://")):
            return cleaned
        return f"/media/{quote(self._media_relpath(tid, cleaned).lstrip('/'))}"

    def _guess_media_content_type(self, target: Path, data: bytes) -> str:
        guessed = mimetypes.guess_type(str(target))[0]
        if guessed:
            return guessed
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return "image/webp"
        return "application/octet-stream"

    def _confirm_title_review(self) -> None:
        form = self._form()
        raw_tid = form.get("tid", "").strip()
        if not raw_tid.isdigit():
            self._send("tid is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            before, after = ThreadsRepository(conn).confirm_title_review(tid)
            AuditEventsRepository(conn).record(
                actor="web",
                action="confirm_title_review",
                target_type="thread",
                target_id=str(tid),
                before=before,
                after=after,
            )
            conn.commit()
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url("/title-review"))
            self.end_headers()
        except ValueError as exc:
            conn.rollback()
            self._send(str(exc), HTTPStatus.NOT_FOUND, "text/plain")
        finally:
            conn.close()

    def _update_title_review(self) -> None:
        form = self._form()
        raw_tid = form.get("tid", "").strip()
        if not raw_tid.isdigit():
            self._send("tid is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        display_title = form.get("display_title", "").strip()
        core_title_guess = form.get("core_title_guess", "").strip()
        series_key = form.get("series_key", "").strip()
        try:
            chapter_index = self._parse_optional_float(form.get("chapter_index"))
            chapter_index_end = self._parse_optional_float(form.get("chapter_index_end"))
            confidence = self._parse_optional_float(form.get("confidence"))
        except ValueError as exc:
            self._send(str(exc), HTTPStatus.BAD_REQUEST, "text/plain")
            return

        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            before, after = ThreadsRepository(conn).update_title_review(
                tid,
                display_title=display_title,
                group_name=self._clean_optional_text(form.get("group_name")),
                author_guess=self._clean_optional_text(form.get("author_guess")),
                core_title_guess=core_title_guess,
                series_key=series_key,
                title_aliases=self._parse_multiline_list(form.get("title_aliases")),
                chapter_name=self._clean_optional_text(form.get("chapter_name")),
                chapter_index=chapter_index,
                chapter_index_end=chapter_index_end,
                chapter_title=self._clean_optional_text(form.get("chapter_title")),
                subtitle=self._clean_optional_text(form.get("subtitle")),
                tags=self._parse_multiline_list(form.get("tags")),
                confidence=confidence,
                needs_review=False,
            )
            AuditEventsRepository(conn).record(
                actor="web",
                action="update_title_review",
                target_type="thread",
                target_id=str(tid),
                before=before,
                after=after,
            )
            conn.commit()
            title_after = after.get("title_parse") if isinstance(after, dict) else None
            if isinstance(title_after, dict):
                update_title_hints(
                    self.settings,
                    group_name=title_after.get("group_name"),
                    author_guess=title_after.get("author_guess"),
                )
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url("/title-review"))
            self.end_headers()
        except ValueError as exc:
            conn.rollback()
            self._send(str(exc), HTTPStatus.BAD_REQUEST, "text/plain")
        finally:
            conn.close()

    def _confirm_series_review(self) -> None:
        form = self._form()
        raw_series_id = form.get("series_id", "").strip()
        if not raw_series_id.isdigit():
            self._send("series_id is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        series_id = int(raw_series_id)
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            before, after = SeriesRepository(conn).confirm_series_review(series_id)
            AuditEventsRepository(conn).record(
                actor="web",
                action="confirm_series_review",
                target_type="series",
                target_id=str(series_id),
                before=before,
                after=after,
            )
            conn.commit()
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url("/title-review"))
            self.end_headers()
        except ValueError as exc:
            conn.rollback()
            self._send(str(exc), HTTPStatus.NOT_FOUND, "text/plain")
        finally:
            conn.close()

    def _merge_series_review(self) -> None:
        form = self._form()
        source_raw = form.get("source_series_id", "").strip()
        target_raw = form.get("target_series_id", "").strip()
        return_to = form.get("return_to", "").strip()
        if not source_raw.isdigit() or not target_raw.isdigit():
            self._send("source_series_id and target_series_id are required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        source_series_id = int(source_raw)
        target_series_id = int(target_raw)
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            before, after = SeriesRepository(conn).merge_series(source_series_id, target_series_id)
            AuditEventsRepository(conn).record(
                actor="web",
                action="merge_series",
                target_type="series",
                target_id=str(target_series_id),
                before=before,
                after=after,
            )
            conn.commit()
            self.send_response(HTTPStatus.SEE_OTHER.value)
            location = self._url(f"/series/{target_series_id}") if return_to == "series_detail" else self._url("/title-review")
            self.send_header("Location", location)
            self.end_headers()
        except ValueError as exc:
            conn.rollback()
            self._send(str(exc), HTTPStatus.NOT_FOUND, "text/plain")
        finally:
            conn.close()

    def _create_rebuild_series_job(self) -> None:
        self._form()
        conn, repo = self._repo()
        try:
            job = repo.create(JobType.TITLE_REFINE.value, payload={"mode": "rebuild_series"})
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url(f"/jobs?created={job.job_id}"))
            self.end_headers()
        finally:
            conn.close()

    def _create_export_thread(self) -> None:
        form = self._form()
        raw_tid = form.get("tid", "").strip()
        if not raw_tid.isdigit():
            self._send("tid is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        strategy = form.get("strategy", "").strip() or self.settings.export_default_strategy
        conn, repo = self._repo()
        try:
            job = repo.create(JobType.EXPORT_THREAD.value, tid=tid, payload={"tid": tid, "strategy": strategy})
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url(f"/jobs?created={job.job_id}"))
            self.end_headers()
        finally:
            conn.close()

    def _delete_thread(self) -> None:
        form = self._form()
        raw_tid = form.get("tid", "").strip()
        if not raw_tid.isdigit():
            self._send("tid is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            threads_repo = ThreadsRepository(conn)
            before, after = threads_repo.delete_thread(tid)
            AuditEventsRepository(conn).record(
                actor="web",
                action="delete_thread",
                target_type="thread",
                target_id=str(tid),
                before=before,
                after=after,
            )
            conn.commit()
            self._delete_thread_files(tid, before.get("thread") if isinstance(before, dict) else None)
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url("/threads"))
            self.end_headers()
        except ValueError as exc:
            conn.rollback()
            self._send(str(exc), HTTPStatus.NOT_FOUND, "text/plain")
        finally:
            conn.close()

    def _delete_series(self) -> None:
        form = self._form()
        raw_series_id = form.get("series_id", "").strip()
        if not raw_series_id.isdigit():
            self._send("series_id is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        series_id = int(raw_series_id)
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            repo = SeriesRepository(conn)
            before, after = repo.delete_series(series_id)
            AuditEventsRepository(conn).record(
                actor="web",
                action="delete_series",
                target_type="series",
                target_id=str(series_id),
                before=before,
                after=after,
            )
            conn.commit()
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url("/series"))
            self.end_headers()
        except ValueError as exc:
            conn.rollback()
            status = HTTPStatus.BAD_REQUEST if "still has" in str(exc) else HTTPStatus.NOT_FOUND
            self._send(str(exc), status, "text/plain")
        finally:
            conn.close()

    def _delete_thread_files(self, tid: int, thread_row: dict[str, object] | None) -> None:
        thread_dir = self.settings.data_dir / "threads" / str(tid)
        if thread_dir.exists():
            shutil.rmtree(thread_dir, ignore_errors=True)
        export_value = None if not thread_row else thread_row.get("export_path")
        if export_value:
            export_path = Path(str(export_value))
            if not export_path.is_absolute():
                export_path = self.settings.data_dir / export_path
            try:
                if export_path.exists():
                    export_path.unlink()
            except OSError:
                pass

    def _create_noop(self) -> None:
        conn, repo = self._repo()
        try:
            job = repo.create(JobType.NOOP.value)
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url(f"/jobs?created={job.job_id}"))
            self.end_headers()
        finally:
            conn.close()

    def _create_sync_thread(self) -> None:
        form = self._form()
        html_path = form.get("html_path", "").strip()
        raw_tid = form.get("tid", "").strip()
        url = form.get("url", "").strip()
        base_url = form.get("base_url", "").strip()
        tid = int(raw_tid) if raw_tid.isdigit() else None
        if not html_path and tid is None and not url:
            self._send("html_path or tid or url is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        conn, repo = self._repo()
        try:
            payload = {key: value for key, value in {"html_path": html_path or None, "tid": tid, "url": url or None, "base_url": base_url or None}.items() if value is not None}
            job = repo.create(JobType.SYNC_THREAD.value, tid=tid, payload=payload)
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url(f"/jobs?created={job.job_id}"))
            self.end_headers()
        finally:
            conn.close()

    def _create_resync_thread(self) -> None:
        form = self._form()
        raw_tid = form.get("tid", "").strip()
        if not raw_tid.isdigit():
            self._send("tid is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        conn, repo = self._repo()
        try:
            job = repo.create(JobType.SYNC_THREAD.value, tid=tid, payload={"tid": tid})
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url(f"/jobs?created={job.job_id}"))
            self.end_headers()
        finally:
            conn.close()

    def _create_reexport_thread(self) -> None:
        form = self._form()
        raw_tid = form.get("tid", "").strip()
        if not raw_tid.isdigit():
            self._send("tid is required", HTTPStatus.BAD_REQUEST, "text/plain")
            return
        tid = int(raw_tid)
        conn, repo = self._repo()
        try:
            job = repo.create(
                JobType.EXPORT_THREAD.value,
                tid=tid,
                payload={"tid": tid, "strategy": "force_resync"},
            )
            self.send_response(HTTPStatus.SEE_OTHER.value)
            self.send_header("Location", self._url(f"/jobs?created={job.job_id}"))
            self.end_headers()
        finally:
            conn.close()


def run(settings: Settings) -> None:
    server = build_server(settings)
    try:
        print(f"YamiboMCP web console listening on http://{settings.web_host}:{settings.web_port}")
        server.serve_forever()
    finally:
        server.server_close()


def build_server(settings: Settings) -> ThreadingHTTPServer:
    WebHandler.settings = settings
    return ThreadingHTTPServer((settings.web_host, settings.web_port), WebHandler)


class EmbeddedWebServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.server = build_server(settings)
        self.thread = threading.Thread(target=self.server.serve_forever, name="yamibo-web", daemon=True)

    def start(self) -> None:
        print(f"YamiboMCP web console listening on http://{self.settings.web_host}:{self.settings.web_port}")
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run YamiboMCP local web console.")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    configure_logging()
    settings = load_settings()
    if args.host or args.port:
        settings = Settings(
            project_root=settings.project_root,
            config_path=settings.config_path,
            data_dir=settings.data_dir,
            db_path=settings.db_path,
            title_hints_path=settings.title_hints_path,
            web_host=args.host or settings.web_host,
            web_port=args.port or settings.web_port,
            worker_id=settings.worker_id,
            worker_poll_seconds=settings.worker_poll_seconds,
            worker_lease_seconds=settings.worker_lease_seconds,
            worker_heartbeat_seconds=settings.worker_heartbeat_seconds,
            cookie_file=settings.cookie_file,
            login_username=settings.login_username,
            login_password=settings.login_password,
            use_system_proxy=settings.use_system_proxy,
            image_download_timeout_seconds=settings.image_download_timeout_seconds,
            image_download_retries=settings.image_download_retries,
            export_dir=settings.export_dir,
            export_default_strategy=settings.export_default_strategy,
            export_stale_after_hours=settings.export_stale_after_hours,
            llm_base_url=settings.llm_base_url,
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
            title_parse_use_llm=settings.title_parse_use_llm,
            common_scanlation_groups=settings.common_scanlation_groups,
            common_authors=settings.common_authors,
            backup_dir=settings.backup_dir,
            backup_keep_count=settings.backup_keep_count,
            cleanup_staging_older_than_hours=settings.cleanup_staging_older_than_hours,
        )
    run(settings)


if __name__ == "__main__":
    main()
