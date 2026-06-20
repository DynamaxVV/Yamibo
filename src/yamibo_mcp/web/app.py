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
from yamibo_mcp.db.repositories.assets import AssetsRepository
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.job_events import JobEventsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.series import SeriesRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobType
from yamibo_mcp.domain.forums import default_forums
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.services.title_hints import update_title_hints
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.web.api import handle_api

_FORUM_NAMES: dict[int, str] = {f.forum_id: f.name for f in default_forums()}
_FORUM_KINDS: dict[int, str] = {f.forum_id: f.content_kind for f in default_forums()}

TRANSLATIONS = {
    "zh": {
        "dashboard": "控制台",
        "jobs": "任务",
        "threads": "帖子",
        "series": "系列",
        "title_review": "复核",
        "exports": "导出",
        "forums": "版块",
        "language": "语言",
        "switch_zh": "中文",
        "switch_en": "EN",
        "data_dir": "数据目录",
        "create_noop_job": "创建空任务",
        "local_html_path": "本地 HTML 路径",
        "create_sync_job": "创建同步任务",
        "recent_jobs": "最近任务",
        "worker_heartbeats": "Worker 心跳",
        "recent_audit_events": "审计事件",
        "id": "ID",
        "type": "类型",
        "status": "状态",
        "stage": "阶段",
        "error": "错误",
        "updated": "更新时间",
        "worker": "Worker",
        "latest_heartbeat": "最近心跳",
        "latest_update": "最近更新",
        "running_jobs": "运行中",
        "seen_jobs": "已处理",
        "action": "操作",
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
        "needs_review": "复核",
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
        "metadata_preview": "Metadata",
        "context_preview": "Context",
        "reading_preview": "阅读预览",
        "reading_preview_tip": "按楼层顺序合并展示正文和本地归档图片。",
        "image_width": "图片宽度",
        "image_width_50": "50%",
        "image_width_75": "75%",
        "image_width_100": "100%",
        "back_to_top": "顶部",
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
        "title_parse_log_preview": "标题日志",
        "events_timeline": "事件时间线",
        "event_type": "事件类型",
        "diagnostics": "诊断",
        "job_diagnostics_intro": "用于判断任务卡在哪一段，以及数据有没有落干净。",
        "job_hint_snapshot": "snapshot.json 存在：HTML 解析已成功，问题可能在校验、图片下载或 DB 写入。",
        "job_hint_failure": "failure.json 存在：看 stage、error_type、error_message 定位失败边界。",
        "job_hint_title_parse_log": "title_parse_log.json 存在：对比规则 baseline 和 LLM 结果。",
        "job_hint_partial": "partial 状态：帖子已归档但有图片缺失。",
        "schema_version": "Schema 版本",
        "thread_count": "帖子数",
        "series_count": "系列数",
        "audit_event_count": "审计事件数",
        "view_thread": "查看帖子",
        "quick_actions": "快捷操作",
        "remote_url": "远端 URL",
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
        "cannot_delete_nonempty_series": "仅允许删除空系列。",
        "deleted": "已删除",
        "archive_meta": "归档信息",
        "title_meta": "标题信息",
        "status_meta": "状态",
        "open_thread": "打开",
        "open_job": "打开",
        "open_series": "打开",
        "job_meta": "任务",
        "error_meta": "错误",
        "export_meta": "导出",
        "overview": "概览",
        "archive_ready": "归档完成",
        "archive_partial": "部分完成",
        "archive_failed": "归档失败",
        "start_sync": "发起同步",
        "thread_not_found": "帖子不存在",
        "images": "图片预览",
        "no_images": "暂无归档图片。",
        "open_image": "原图",
        "image_preview_tip": "本地归档图片，用于核验内容完整性。",
        "recent_archived_threads": "最近归档",
        "no_archived_threads": "暂无归档帖子。",
        "no_review_items": "暂无待复核标题。",
        "subtitle": "副标题",
        "tags": "标签",
        "chapter_index": "章节序号",
        "chapter_index_end": "章节结束序号",
        "one_per_line": "每行一个",
        "forum_name": "版块",
        "forum_id": "版块 ID",
        "content_kind": "内容类型",
        "enabled": "启用",
        "yes": "是",
        "no": "否",
        "asset_id": "资源 ID",
        "asset_type": "资源类型",
        "remote_url_label": "远端 URL",
        "local_path": "本地路径",
        "downloaded": "已下载",
        "missing": "缺失",
        "pending": "等待中",
        "skipped": "跳过",
        "block_type": "块类型",
        "order_index": "序号",
        "media_type": "媒体类型",
        "assets": "资源",
        "content_blocks": "内容块",
        "threads_label": "帖子数",
    },
    "en": {
        "dashboard": "Dashboard",
        "jobs": "Jobs",
        "threads": "Threads",
        "series": "Series",
        "title_review": "Review",
        "exports": "Exports",
        "forums": "Forums",
        "language": "Language",
        "switch_zh": "中文",
        "switch_en": "EN",
        "data_dir": "Data dir",
        "create_noop_job": "Create no-op job",
        "local_html_path": "Local HTML path",
        "create_sync_job": "Create sync job",
        "recent_jobs": "Recent Jobs",
        "worker_heartbeats": "Worker Heartbeats",
        "recent_audit_events": "Audit Events",
        "id": "ID",
        "type": "Type",
        "status": "Status",
        "stage": "Stage",
        "error": "Error",
        "updated": "Updated",
        "worker": "Worker",
        "latest_heartbeat": "Last heartbeat",
        "latest_update": "Last update",
        "running_jobs": "Running",
        "seen_jobs": "Seen",
        "action": "Action",
        "target": "Target",
        "actor": "Actor",
        "at": "At",
        "status_filters": "Status",
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
        "created_job": "Job created",
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
        "needs_review": "Review",
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
        "create_export_job": "Create export",
        "raw_title": "Raw title",
        "publisher": "Publisher",
        "group_name": "Group",
        "author_guess": "Author",
        "chapter_title": "Chapter title",
        "context_path": "Context path",
        "export_path": "Export path",
        "open_metadata": "Open metadata.json",
        "open_context": "Open context.md",
        "metadata_preview": "Metadata",
        "context_preview": "Context",
        "reading_preview": "Reading Preview",
        "reading_preview_tip": "Merged floor text and archived images in reading order.",
        "image_width": "Image width",
        "image_width_50": "50%",
        "image_width_75": "75%",
        "image_width_100": "100%",
        "back_to_top": "Top",
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
        "failure_preview": "Failure",
        "snapshot_preview": "Snapshot",
        "title_parse_log_preview": "Title Log",
        "events_timeline": "Events Timeline",
        "event_type": "Event type",
        "diagnostics": "Diagnostics",
        "job_diagnostics_intro": "Use these to see where the job stopped and whether data made it through.",
        "job_hint_snapshot": "snapshot.json exists: HTML parsing succeeded.",
        "job_hint_failure": "failure.json exists: check stage, error_type, error_message.",
        "job_hint_title_parse_log": "title_parse_log.json exists: compare rule baseline with LLM result.",
        "job_hint_partial": "partial: thread archived but some images missing.",
        "schema_version": "Schema version",
        "thread_count": "Threads",
        "series_count": "Series",
        "audit_event_count": "Audit events",
        "view_thread": "View",
        "quick_actions": "Quick Actions",
        "remote_url": "Remote URL",
        "base_url": "Base URL",
        "sync_thread_now": "Create sync job",
        "resync_thread": "Resync",
        "force_resync_export": "Resync + export",
        "export_strategy": "Export strategy",
        "cache_only": "Cache only",
        "sync_if_stale": "Sync if stale",
        "force_resync": "Force resync",
        "open_jobs": "Jobs",
        "open_threads": "Threads",
        "operations": "Actions",
        "delete_thread": "Delete",
        "delete_series": "Delete",
        "cannot_delete_nonempty_series": "Only empty series can be deleted.",
        "deleted": "Deleted",
        "archive_meta": "Archive",
        "title_meta": "Title",
        "status_meta": "Status",
        "open_thread": "Open",
        "open_job": "Open",
        "open_series": "Open",
        "job_meta": "Job",
        "error_meta": "Error",
        "export_meta": "Export",
        "overview": "Overview",
        "archive_ready": "Ready",
        "archive_partial": "Partial",
        "archive_failed": "Failed",
        "start_sync": "Start sync",
        "thread_not_found": "Thread not found",
        "images": "Images",
        "no_images": "No archived images.",
        "open_image": "Open",
        "image_preview_tip": "Archived images for content verification.",
        "recent_archived_threads": "Recent Archives",
        "no_archived_threads": "No archived threads yet.",
        "no_review_items": "No review items.",
        "subtitle": "Subtitle",
        "tags": "Tags",
        "chapter_index": "Chapter index",
        "chapter_index_end": "Chapter index (end)",
        "one_per_line": "One per line",
        "forum_name": "Forum",
        "forum_id": "Forum ID",
        "content_kind": "Kind",
        "enabled": "Enabled",
        "yes": "Yes",
        "no": "No",
        "asset_id": "Asset ID",
        "asset_type": "Type",
        "remote_url_label": "Remote URL",
        "local_path": "Local path",
        "downloaded": "Downloaded",
        "missing": "Missing",
        "pending": "Pending",
        "skipped": "Skipped",
        "block_type": "Block type",
        "order_index": "Order",
        "media_type": "Media type",
        "assets": "Assets",
        "content_blocks": "Content Blocks",
        "threads_label": "Threads",
    },
}


def _forum_name(forum_id: int | None) -> str:
    if forum_id is None:
        return "-"
    return _FORUM_NAMES.get(forum_id, f"forum-{forum_id}")


def _forum_kind(forum_id: int | None) -> str:
    if forum_id is None:
        return "-"
    return _FORUM_KINDS.get(forum_id, "unknown")


def _badge(status: str | None, kind: str = "muted") -> str:
    """Render a status badge. kind: ok/warn/error/accent/muted"""
    text = html.escape(str(status or "-"))
    return f'<span class="badge badge-{kind}">{text}</span>'


def _status_badge(status: str | None) -> str:
    s = (status or "").lower()
    if s in ("succeeded", "complete", "valid", "downloaded"):
        return _badge(status, "ok")
    if s in ("running", "queued", "retrying", "pending"):
        return _badge(status, "accent")
    if s in ("partial",):
        return _badge(status, "warn")
    if s in ("failed", "error", "interrupted", "missing"):
        return _badge(status, "error")
    return _badge(status, "muted")


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


# ─── CSS Design System ───

CSS = """
:root {
  --bg-page: #fafbfc;
  --bg-surface: #ffffff;
  --bg-muted: #f6f8fa;
  --bg-header: #f0f2f5;
  --border: #d1d9e0;
  --border-light: #e8ecf0;
  --text-primary: #1f2328;
  --text-secondary: #656d76;
  --text-tertiary: #8b949e;
  --accent: #0969da;
  --accent-light: #ddf4ff;
  --accent-text: #0550ae;
  --status-ok: #1a7f37;
  --status-warn: #9a6700;
  --status-error: #cf222e;
  --status-muted: #8b949e;
}
*, *::before, *::after { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
  font-size: 14px; line-height: 1.5; color: var(--text-primary);
  background: var(--bg-page); margin: 0;
}
.page { max-width: 1400px; margin: 0 auto; padding: 16px 24px; }

/* Topbar */
.topbar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 0; border-bottom: 1px solid var(--border-light); margin-bottom: 16px;
}
.brand { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.subnav { display: flex; gap: 16px; }
.subnav a {
  color: var(--text-secondary); text-decoration: none; font-size: 13px;
  padding: 4px 0; border-bottom: 2px solid transparent;
}
.subnav a:hover { color: var(--accent); }
.subnav a.active { color: var(--accent); border-bottom-color: var(--accent); }
.lang-switch { font-size: 12px; color: var(--text-tertiary); }
.lang-switch a { color: var(--text-secondary); text-decoration: none; }
.lang-switch a:hover { color: var(--accent); }

/* Headings */
h1 { font-size: 20px; font-weight: 600; margin: 0 0 16px; }
h2 {
  font-size: 11px; font-weight: 600; margin: 20px 0 8px;
  color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.8px;
}

/* Stat row */
.stat-row {
  display: flex; gap: 1px; margin-bottom: 16px;
  background: var(--border-light); border: 1px solid var(--border-light); border-radius: 3px;
  overflow: hidden;
}
.stat-cell { flex: 1; padding: 12px 16px; background: var(--bg-surface); }
.stat-cell .label { font-size: 12px; color: var(--text-tertiary); }
.stat-cell .value { font-size: 20px; font-weight: 600; margin-top: 2px; }

/* Tables */
.table-wrap { overflow-x: auto; margin-bottom: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td {
  padding: 8px 12px; text-align: left;
  border-bottom: 1px solid var(--border-light); vertical-align: top;
}
th {
  font-weight: 600; font-size: 11px; color: var(--text-tertiary);
  text-transform: uppercase; letter-spacing: 0.3px;
  background: var(--bg-header); position: sticky; top: 0; z-index: 1;
}
tr:hover td { background: var(--bg-muted); }
td.mono { font-family: ui-monospace, "SFMono-Regular", "SF Mono", Menlo, monospace; font-size: 12px; }
td.nowrap { white-space: nowrap; }
td.truncate { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
td a { color: var(--accent); text-decoration: none; }
td a:hover { text-decoration: underline; }

/* Badges */
.badge {
  display: inline-block; padding: 1px 8px; font-size: 12px; font-weight: 500;
  border-radius: 999px; white-space: nowrap;
}
.badge-ok { background: #dafbe1; color: var(--status-ok); }
.badge-warn { background: #fff8c5; color: var(--status-warn); }
.badge-error { background: #ffebe9; color: var(--status-error); }
.badge-muted { background: var(--bg-muted); color: var(--status-muted); }
.badge-accent { background: var(--accent-light); color: var(--accent-text); }

/* Forms */
input[type="text"], input[type="number"], textarea, select {
  padding: 5px 8px; border: 1px solid var(--border); border-radius: 3px;
  font-size: 13px; background: var(--bg-surface); font-family: inherit;
}
input:focus, textarea:focus, select:focus {
  border-color: var(--accent); outline: none;
  box-shadow: 0 0 0 2px rgba(9,105,218,0.15);
}
textarea { width: 100%; min-height: 72px; resize: vertical; box-sizing: border-box; }
button {
  padding: 5px 12px; border: 1px solid var(--border); border-radius: 3px;
  background: var(--bg-header); cursor: pointer; font-size: 13px; font-family: inherit;
}
button:hover { background: var(--bg-muted); }
button.primary { background: var(--accent); color: white; border-color: var(--accent); }
button.primary:hover { background: var(--accent-text); }
button.danger { background: var(--status-error); color: white; border-color: var(--status-error); }
button.danger:hover { background: #a21c25; }
button:disabled { opacity: 0.5; cursor: not-allowed; }

/* Actions row */
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.actions form { margin: 0; }

/* Toolbar */
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }

/* Segmented filters */
.segmented { display: inline-flex; flex-wrap: wrap; gap: 0; margin-bottom: 12px; }
.segmented a {
  padding: 5px 12px; border: 1px solid var(--border); background: var(--bg-surface);
  color: var(--text-secondary); text-decoration: none; font-size: 13px;
  margin-right: -1px;
}
.segmented a:first-child { border-radius: 3px 0 0 3px; }
.segmented a:last-child { border-radius: 0 3px 3px 0; }
.segmented a.active {
  background: var(--accent-light); color: var(--accent-text);
  border-color: var(--accent); z-index: 1; position: relative;
}
.segmented a:hover:not(.active) { background: var(--bg-muted); }

/* Panel */
.panel {
  background: var(--bg-surface); border: 1px solid var(--border-light);
  padding: 16px; margin-bottom: 16px;
}

/* Hint list */
.hint-list { margin: 4px 0 0; padding-left: 20px; color: var(--text-secondary); font-size: 13px; }
.hint-list li { margin: 4px 0; }

/* Artifact links */
.artifact-links { display: flex; flex-wrap: wrap; gap: 12px; }
.artifact-links a { color: var(--accent); text-decoration: none; font-size: 13px; }
.artifact-links a:hover { text-decoration: underline; }

/* Reading view */
.reading-flow { display: flex; flex-direction: column; gap: 12px; }
.floor-block {
  background: var(--bg-surface); border: 1px solid var(--border-light); padding: 12px;
}
.floor-head { font-weight: 600; font-size: 13px; margin-bottom: 8px; color: var(--text-secondary); }
.floor-body { white-space: pre-wrap; word-break: break-word; line-height: 1.7; font-size: 14px; }
.floor-images { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.floor-images a { display: block; }
.floor-images img {
  display: block; width: min(var(--reading-image-width, 100%), 100%);
  max-width: 100%; height: auto; background: var(--bg-muted);
}

/* Image grid */
.image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
.image-card { background: var(--bg-surface); border: 1px solid var(--border-light); padding: 8px; }
.image-card img { display: block; width: 100%; height: auto; background: var(--bg-muted); }
.image-card .meta { margin-top: 6px; font-size: 12px; color: var(--text-tertiary); }

/* Review cards */
details.review-card { background: var(--bg-surface); border: 1px solid var(--border-light); margin-bottom: 8px; }
details.review-card summary {
  list-style: none; cursor: pointer; padding: 10px 12px; font-size: 13px;
}
details.review-card summary::-webkit-details-marker { display: none; }
details.review-card summary:hover { background: var(--bg-muted); }
.review-meta {
  display: grid; grid-template-columns: minmax(80px, 120px) 1fr;
  gap: 6px 12px; margin: 0 0 12px; font-size: 13px;
}
.review-form { padding: 12px; border-top: 1px solid var(--border-light); }
.review-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.field label { display: block; font-size: 12px; color: var(--text-tertiary); margin-bottom: 4px; }
.field input[type="text"], .field input[type="number"] { width: 100%; box-sizing: border-box; }
.field-full { grid-column: 1 / -1; }

/* Back to top */
.back-to-top {
  position: fixed; right: 20px; bottom: 20px; z-index: 20;
  display: inline-flex; align-items: center; justify-content: center;
  padding: 8px 12px; border: 1px solid var(--border); border-radius: 3px;
  background: var(--bg-surface); color: var(--text-secondary);
  text-decoration: none; font-size: 12px;
}
.back-to-top:hover { background: var(--bg-muted); color: var(--accent); }

/* Responsive */
@media (max-width: 768px) {
  .page { padding: 12px; }
  .stat-row { flex-direction: column; }
  .subnav { gap: 8px; flex-wrap: wrap; }
  table { font-size: 12px; }
  th, td { padding: 6px 8px; }
}
"""


class WebHandler(BaseHTTPRequestHandler):
    settings: Settings
    lang: str = "zh"

    # ─── Helpers ───

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

    def _preview(self, value: str | None, *, limit: int = 4000) -> str:
        if not value:
            return ""
        if len(value) <= limit:
            return value
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
            HAVING SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) > 0
            ORDER BY COALESCE(MAX(heartbeat_at), MAX(updated_at)) DESC
            LIMIT 20
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def _job_artifact_path(self, job_id: str, filename: str) -> Path:
        return self._storage_paths().staging_job_dir(job_id) / filename

    def _thread_image_width_toolbar(self, tid: int, *, img_width: int) -> str:
        options = [50, 75, 100]
        links = "".join(
            f'<a class="{"active" if option == img_width else ""}" href="{self._thread_detail_url(tid, img_width=option)}">{self._t(f"image_width_{option}")}</a>'
            for option in options
        )
        return f'<div class="segmented">{links}</div>'

    # ─── HTML Shell ───

    def _html_page(self, title: str, body: str, *, auto_refresh_seconds: int | None = None) -> str:
        app_title = "YamiboMCP" if self.lang == "zh" else "YamiboMCP"
        nav_items = [
            ("/", "dashboard"), ("/jobs", "jobs"), ("/threads", "threads"),
            ("/series", "series"), ("/title-review", "title_review"),
            ("/exports", "exports"), ("/forums", "forums"),
        ]
        nav_links = "".join(
            f'<a href="{self._url(path)}" class="{"active" if self.path == path or (path != "/" and self.path.startswith(path)) else ""}">{self._t(key)}</a>'
            for path, key in nav_items
        )
        nav = (
            f'<div class="topbar">'
            f'<div style="display:flex;align-items:center;gap:20px;">'
            f'<span class="brand">{html.escape(app_title)}</span>'
            f'<div class="subnav">{nav_links}</div></div>'
            f'<div class="lang-switch">'
            f'<a href="{self._url_with_lang("zh")}">{self._t("switch_zh")}</a> · '
            f'<a href="{self._url_with_lang("en")}">{self._t("switch_en")}</a></div></div>'
        )
        meta_refresh = f'<meta http-equiv="refresh" content="{auto_refresh_seconds}">' if auto_refresh_seconds and auto_refresh_seconds > 0 else ""
        return f"""<!DOCTYPE html>
<html lang="{self.lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · {html.escape(app_title)}</title>
{meta_refresh}
<style>{CSS}</style>
</head><body>
<div id="top"></div>
<div class="page">
{nav}
<h1>{html.escape(title)}</h1>
{body}
</div>
<a class="back-to-top" href="#top">{self._t("back_to_top")}</a>
</body></html>"""

    # ─── Routing ───

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._activate_lang(parsed.query)

        # API routes
        if parsed.path.startswith("/api/"):
            handle_api(self, parsed.path, parsed.query, self.settings)
            return

        # Static files (React SPA)
        if self._serve_static(parsed.path):
            return

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
        elif parsed.path == "/forums":
            self._forums()
        elif parsed.path == "/":
            self._dashboard()
        else:
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._activate_lang(parsed.query)

        # API routes
        if parsed.path.startswith("/api/"):
            handle_api(self, parsed.path, parsed.query, self.settings)
            return

        handler = {
            "/jobs/noop": self._create_noop,
            "/jobs/sync-thread": self._create_sync_thread,
            "/jobs/resync-thread": self._create_resync_thread,
            "/threads/delete": self._delete_thread,
            "/series/delete": self._delete_series,
            "/title-review/confirm-title": self._confirm_title_review,
            "/title-review/update-title": self._update_title_review,
            "/title-review/confirm-series": self._confirm_series_review,
            "/title-review/merge-series": self._merge_series_review,
            "/jobs/rebuild-series": self._create_rebuild_series_job,
            "/jobs/export-thread": self._create_export_thread,
            "/jobs/reexport-thread": self._create_reexport_thread,
        }.get(parsed.path)
        if handler:
            handler()
        else:
            self._send("Not found", HTTPStatus.NOT_FOUND, "text/plain")

    # ─── Dashboard ───

    def _dashboard(self) -> None:
        conn, repo = self._repo()
        try:
            recent_jobs = repo.list(limit=10)
            recent_audits = AuditEventsRepository(conn).list_recent(limit=8)
            worker_rows = self._worker_heartbeats(conn)
            archived_threads = ThreadsRepository(conn).list_threads(limit=20)
            thread_count = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            series_count = conn.execute("SELECT COUNT(*) FROM series").fetchone()[0]
            finished_exports = conn.execute("SELECT COUNT(*) FROM threads WHERE is_exported = 1").fetchone()[0]

            stats = (
                f'<div class="stat-row">'
                f'<div class="stat-cell"><div class="label">{self._t("thread_count")}</div><div class="value">{thread_count}</div></div>'
                f'<div class="stat-cell"><div class="label">{self._t("series_count")}</div><div class="value">{series_count}</div></div>'
                f'<div class="stat-cell"><div class="label">{self._t("exports")}</div><div class="value">{finished_exports}</div></div>'
                f'<div class="stat-cell"><div class="label">{self._t("data_dir")}</div><div class="value" style="font-size:13px;word-break:break-all">{html.escape(str(self.settings.data_dir))}</div></div>'
                f'</div>'
            )

            # Jobs table
            job_rows = "".join(self._render_job_tr(job) for job in recent_jobs)
            jobs_table = (
                f'<h2>{self._t("recent_jobs")}</h2>'
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("id")}</th><th>{self._t("type")}</th><th>{self._t("status")}</th>'
                f'<th>{self._t("stage")}</th><th>{self._t("tid")}</th><th>{self._t("updated")}</th></tr>'
                f'{job_rows}</table></div>'
            )

            # Workers table
            if worker_rows:
                worker_trs = "".join(
                    f'<tr><td class="mono">{html.escape(str(r["worker_id"] or ""))}</td>'
                    f'<td>{r["running_jobs"]}</td><td>{r["seen_jobs"]}</td>'
                    f'<td class="nowrap">{html.escape(str(r["latest_heartbeat_at"] or "-"))}</td></tr>'
                    for r in worker_rows
                )
                workers_table = (
                    f'<h2>{self._t("worker_heartbeats")}</h2>'
                    f'<div class="table-wrap"><table>'
                    f'<tr><th>{self._t("worker")}</th><th>{self._t("running_jobs")}</th>'
                    f'<th>{self._t("seen_jobs")}</th><th>{self._t("latest_heartbeat")}</th></tr>'
                    f'{worker_trs}</table></div>'
                )
            else:
                workers_table = ""

            # Audit table
            audit_trs = "".join(
                f'<tr><td>{html.escape(r["action"])}</td>'
                f'<td>{html.escape(r["target_type"])}:{html.escape(r["target_id"])}</td>'
                f'<td>{html.escape(r["actor"])}</td>'
                f'<td class="nowrap">{html.escape(r["created_at"])}</td></tr>'
                for r in recent_audits
            )
            audit_table = (
                f'<h2>{self._t("recent_audit_events")}</h2>'
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("action")}</th><th>{self._t("target")}</th>'
                f'<th>{self._t("actor")}</th><th>{self._t("at")}</th></tr>'
                f'{audit_trs}</table></div>'
            )

            # Threads table
            thread_trs = "".join(self._render_thread_tr(row) for row in archived_threads)
            threads_table = (
                f'<h2>{self._t("recent_archived_threads")}</h2>'
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("tid")}</th><th>{self._t("title")}</th><th>{self._t("forum_name")}</th>'
                f'<th>{self._t("archive")}</th><th>{self._t("synced")}</th></tr>'
                f'{thread_trs or f"<tr><td colspan=\"5\" style=\"color:var(--text-tertiary)\">{self._t('no_archived_threads')}</td></tr>"}</table></div>'
            )

            body = stats + jobs_table + workers_table + audit_table + threads_table
            self._send(self._html_page(self._t("dashboard"), body))
        finally:
            conn.close()

    # ─── Jobs ───

    def _jobs(self, query: str) -> None:
        params = parse_qs(query)
        status = params.get("status", [None])[0]
        created = params.get("created", [""])[0].strip()
        conn, repo = self._repo()
        try:
            jobs = repo.list(limit=150, status=status)
            auto_refresh = 3 if any(self._is_active_job_status(job.status) for job in jobs) else None

            filters = "".join(
                f'<a href="{self._url("/jobs" if val is None else f"/jobs?status={val}")}"'
                f' class="{"active" if val == status else ""}">{self._t(label)}</a>'
                for val, label in [
                    (None, "all"), ("queued", "queued"), ("running", "running"),
                    ("succeeded", "succeeded"), ("failed", "failed"), ("interrupted", "interrupted"),
                ]
            )

            created_notice = ""
            if created:
                created_notice = f'<div class="panel" style="margin-bottom:12px"><strong>{self._t("created_job")}:</strong> <a href="{self._url(f"/jobs/{html.escape(created)}")}">{html.escape(created)}</a></div>'

            form_html = (
                f'<form method="post" action="{self._url("/jobs/noop")}">{self._hidden_lang()}'
                f'<button type="submit">{self._t("create_noop_job")}</button></form>'
                f'<form method="post" action="{self._url("/jobs/sync-thread")}" style="margin-top:8px">'
                f'{self._hidden_lang()}<div class="toolbar">'
                f'<input name="html_path" size="30" placeholder="html_sample/thread.html">'
                f'<input name="tid" size="8" placeholder="TID">'
                f'<input name="url" size="30" placeholder="https://bbs.yamibo.com/thread-...">'
                f'<button type="submit">{self._t("sync_thread_now")}</button></div></form>'
            )

            rows = "".join(self._render_job_tr(job) for job in jobs)
            table = (
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("id")}</th><th>{self._t("type")}</th><th>{self._t("status")}</th>'
                f'<th>{self._t("stage")}</th><th>{self._t("tid")}</th><th>{self._t("progress")}</th>'
                f'<th>{self._t("error")}</th><th>{self._t("created")}</th></tr>'
                f'{rows}</table></div>'
            )

            body = created_notice + f'<div class="segmented">{filters}</div>' + form_html + table
            self._send(self._html_page(self._t("jobs"), body, auto_refresh_seconds=auto_refresh))
        finally:
            conn.close()

    def _render_job_tr(self, job: object) -> str:
        progress = f"{job.progress_current}/{job.progress_total or '?'}"
        error = html.escape(job.error_code or "")
        tid_link = f'<a href="{self._url(f"/threads/{job.tid}")}">{job.tid}</a>' if job.tid else "-"
        return (
            f'<tr>'
            f'<td class="mono"><a href="{self._url(f"/jobs/{job.job_id}")}">{html.escape(job.job_id)}</a></td>'
            f'<td class="nowrap">{html.escape(job.job_type)}</td>'
            f'<td>{_status_badge(job.status)}</td>'
            f'<td class="nowrap">{html.escape(job.stage or "-")}</td>'
            f'<td>{tid_link}</td>'
            f'<td class="nowrap">{progress}</td>'
            f'<td class="truncate">{error or "-"}</td>'
            f'<td class="nowrap">{html.escape(job.created_at)}</td>'
            f'</tr>'
        )

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

            # Diagnostics
            diagnostics = self._job_diagnostics(job.status, snapshot_text, failure_text, title_parse_log_text)

            # Detail table
            snapshot_link = f'<a href="{self._url(f"/artifacts/jobs/{html.escape(job_id)}/snapshot.json")}">snapshot.json</a>' if snapshot_text else ""
            failure_link = f'<a href="{self._url(f"/artifacts/jobs/{html.escape(job_id)}/failure.json")}">failure.json</a>' if failure_text else ""
            title_parse_link = f'<a href="{self._url(f"/artifacts/jobs/{html.escape(job_id)}/title_parse_log.json")}">title_parse_log.json</a>' if title_parse_log_text else ""
            staging_links = f'<div class="artifact-links">{snapshot_link}{failure_link}{title_parse_link}</div>' if (snapshot_link or failure_link or title_parse_link) else "-"

            detail_rows = [
                (self._t("id"), f'<span class="mono">{html.escape(job.job_id)}</span>'),
                (self._t("type"), html.escape(job.job_type)),
                (self._t("status"), _status_badge(job.status)),
                (self._t("stage"), html.escape(job.stage or "-")),
                (self._t("tid"), f'<a href="{self._url(f"/threads/{job.tid}")}">{job.tid}</a>' if job.tid else "-"),
                (self._t("progress"), f"{job.progress_current}/{job.progress_total or '?'}"),
                (self._t("worker"), html.escape(job.worker_id or "-")),
                (self._t("heartbeat"), html.escape(job.heartbeat_at or "-")),
                (self._t("lease_until"), html.escape(job.lease_until or "-")),
                (self._t("error"), f'{html.escape(job.error_code or "")} {html.escape(job.error_message or "")}'.strip() or "-"),
                (self._t("created"), html.escape(job.created_at)),
                (self._t("updated"), html.escape(job.updated_at)),
                (self._t("finished"), html.escape(job.finished_at or "-")),
                (self._t("payload"), self._json_block(job.payload)),
                (self._t("artifacts"), self._json_block(job.artifacts)),
                (self._t("staging"), staging_links),
            ]
            detail_trs = "".join(f'<tr><th style="width:120px">{k}</th><td>{v}</td></tr>' for k, v in detail_rows)
            detail_table = f'<div class="table-wrap"><table>{detail_trs}</table></div>'

            # Events timeline
            events_html = self._render_job_events_timeline(events)
            events_section = f'<h2>{self._t("events_timeline")}</h2>{events_html}' if events_html else ""

            # Previews
            previews = ""
            for label, text in [
                (self._t("failure_preview"), failure_text),
                (self._t("snapshot_preview"), snapshot_text),
                (self._t("title_parse_log_preview"), title_parse_log_text),
            ]:
                if text:
                    previews += f'<h2>{label}</h2><pre style="font-size:12px;max-height:400px;overflow:auto;background:var(--bg-muted);padding:12px;border:1px solid var(--border-light)">{html.escape(self._preview(text))}</pre>'

            body = f'<div class="panel"><h2>{self._t("diagnostics")}</h2><p style="color:var(--text-tertiary);font-size:13px;margin:0 0 8px">{self._t("job_diagnostics_intro")}</p>{diagnostics}</div>' + detail_table + events_section + previews
            self._send(self._html_page(f"{self._t('job_detail')} {job_id}", body, auto_refresh_seconds=auto_refresh))
        finally:
            conn.close()

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
            return '<p style="color:var(--text-tertiary);font-size:13px">No diagnostics available yet.</p>'
        return "<ul class=\"hint-list\">" + "".join(f"<li>{html.escape(item)}</li>" for item in hints) + "</ul>"

    def _render_job_events_timeline(self, events: list[object]) -> str:
        if not events:
            return ""
        rows = []
        for e in events:
            payload = getattr(e, "payload", {}) or {}
            rows.append(
                f'<tr><td class="mono">{getattr(e, "event_id", "")}</td>'
                f'<td class="nowrap">{html.escape(getattr(e, "created_at", "") or "")}</td>'
                f'<td class="nowrap">{html.escape(getattr(e, "event_type", "") or "")}</td>'
                f'<td>{_status_badge(getattr(e, "status", None))}</td>'
                f'<td class="nowrap">{html.escape(getattr(e, "stage", "") or "-")}</td>'
                f'<td><pre style="margin:0;font-size:12px;max-width:300px;overflow:auto">{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></td></tr>'
            )
        return (
            '<div class="table-wrap"><table>'
            f'<tr><th>ID</th><th>{html.escape(self._t("created"))}</th>'
            f'<th>{html.escape(self._t("event_type"))}</th>'
            f'<th>{html.escape(self._t("status"))}</th>'
            f'<th>{html.escape(self._t("stage"))}</th>'
            f'<th>{html.escape(self._t("payload"))}</th></tr>'
            f'{"".join(rows)}</table></div>'
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

    # ─── Threads ───

    def _threads(self, query: str) -> None:
        params = parse_qs(query)
        q = params.get("q", [""])[0].strip()
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            repo = ThreadsRepository(conn)
            threads = repo.search_threads(q, limit=200) if q else repo.list_threads(limit=200)
            rows = "".join(self._render_thread_tr(row) for row in threads)
            body = (
                f'<form method="get" action="/threads" class="toolbar">'
                f'{self._hidden_lang()}'
                f'<input name="q" size="50" value="{html.escape(q)}" placeholder="{html.escape(self._t("search_placeholder"))}">'
                f'<button type="submit">{self._t("search")}</button></form>'
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("tid")}</th><th>{self._t("title")}</th><th>{self._t("forum_name")}</th>'
                f'<th>{self._t("content_kind")}</th><th>{self._t("archive")}</th><th>{self._t("synced")}</th></tr>'
                f'{rows}</table></div>'
            )
            self._send(self._html_page(self._t("threads"), body))
        finally:
            conn.close()

    def _render_thread_tr(self, row) -> str:
        tid = int(row["tid"])
        title = html.escape(row["display_title"] or row["raw_title"] or "")
        fid = row["forum_id"] if "forum_id" in row.keys() else None
        kind = row["content_kind"] if "content_kind" in row.keys() else None
        return (
            f'<tr>'
            f'<td class="mono"><a href="{self._url(f"/threads/{tid}")}">{tid}</a></td>'
            f'<td class="truncate"><a href="{self._url(f"/threads/{tid}")}">{title}</a></td>'
            f'<td class="nowrap">{html.escape(_forum_name(fid))}</td>'
            f'<td class="nowrap">{_badge(kind or "-", "accent") if kind and kind != "-" else "-"}</td>'
            f'<td>{_status_badge(row["archive_status"])}</td>'
            f'<td class="nowrap">{html.escape(row["sync_time"] or "-")}</td>'
            f'</tr>'
        )

    def _thread_detail(self, path: str) -> None:
        suffix = path.removeprefix("/threads/").strip("/")
        if suffix.endswith("/context"):
            self._thread_file(suffix.removesuffix("/context"), "context")
            return
        if suffix.endswith("/metadata"):
            self._thread_file(suffix.removesuffix("/metadata"), "metadata")
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

            # Quick actions
            actions_html = (
                f'<div class="actions">'
                f'<form method="post" action="{self._url("/jobs/resync-thread")}">{self._hidden_lang()}<input type="hidden" name="tid" value="{tid}"><button type="submit">{self._t("resync_thread")}</button></form>'
                f'<form method="post" action="{self._url("/jobs/export-thread")}">{self._hidden_lang()}<input type="hidden" name="tid" value="{tid}"><input type="hidden" name="strategy" value="sync_if_stale"><button type="submit">{self._t("create_export_job")}</button></form>'
                f'<form method="post" action="{self._url("/jobs/reexport-thread")}">{self._hidden_lang()}<input type="hidden" name="tid" value="{tid}"><button type="submit">{self._t("force_resync_export")}</button></form>'
                f'<form method="post" action="{self._url("/threads/delete")}">{self._hidden_lang()}<input type="hidden" name="tid" value="{tid}"><button class="danger" type="submit">{self._t("delete_thread")}</button></form>'
                f'</div>'
            )

            # Thread info table
            fid = thread["forum_id"] if "forum_id" in thread.keys() else None
            kind = thread["content_kind"] if "content_kind" in thread.keys() else None
            media = thread["primary_media_type"] if "primary_media_type" in thread.keys() else None
            info_rows = [
                (self._t("tid"), f'<span class="mono">{tid}</span>'),
                (self._t("raw_title"), html.escape(thread["raw_title"] or "")),
                (self._t("publisher"), html.escape(thread["publisher"] or "-")),
                (self._t("forum_name"), f'{html.escape(_forum_name(fid))} ({fid or "-"})'),
                (self._t("content_kind"), _badge(kind or "-", "accent") if kind else "-"),
                (self._t("media_type"), html.escape(media or "-")),
                (self._t("archive"), _status_badge(thread["archive_status"])),
                (self._t("validation"), _status_badge(thread["validation_status"])),
                (self._t("context_path"), html.escape(thread["context_path"] or "-")),
                (self._t("export_path"), html.escape(thread["export_path"] or "-")),
            ]
            info_trs = "".join(f'<tr><th style="width:120px">{k}</th><td>{v}</td></tr>' for k, v in info_rows)

            # Title parse table
            title_rows = ""
            if title is not None:
                tp = [
                    (self._t("core_title"), html.escape(title["core_title_guess"] or "")),
                    (self._t("group_name"), html.escape(title["group_name"] or "")),
                    (self._t("author_guess"), html.escape(title["author_guess"] or "")),
                    (self._t("chapter"), html.escape(title["chapter_name"] or "")),
                    (self._t("chapter_title"), html.escape(title["chapter_title"] or "")),
                    (self._t("series_key"), f'<a href="{self._url(f"/series/{thread["series_id"]}")}">{html.escape(title["series_key"] or "")}</a>' if thread["series_id"] else html.escape(title["series_key"] or "")),
                    (self._t("confidence"), str(title["confidence"] or "-")),
                    (self._t("needs_review"), self._t("yes") if title["needs_review"] else self._t("no")),
                ]
                title_trs = "".join(f'<tr><th style="width:120px">{k}</th><td>{v}</td></tr>' for k, v in tp)
                title_rows = f'<h2>{self._t("title_meta")}</h2><div class="table-wrap"><table>{title_trs}</table></div>'

            # Assets table
            assets = AssetsRepository(conn).list_assets(tid)
            assets_section = ""
            if assets:
                asset_trs = "".join(
                    f'<tr><td class="mono">{html.escape(a["asset_id"][:16])}</td>'
                    f'<td class="nowrap">{html.escape(a["asset_type"])}</td>'
                    f'<td>{_status_badge(a["status"])}</td>'
                    f'<td>{a["pid"]}</td>'
                    f'<td class="truncate">{html.escape(a["remote_url"] or "")}</td>'
                    f'<td class="truncate">{html.escape(a["local_path"] or "-")}</td></tr>'
                    for a in assets
                )
                assets_section = (
                    f'<h2>{self._t("assets")} ({len(assets)})</h2>'
                    f'<div class="table-wrap"><table>'
                    f'<tr><th>{self._t("asset_id")}</th><th>{self._t("asset_type")}</th><th>{self._t("status")}</th>'
                    f'<th>{self._t("pid")}</th><th>{self._t("remote_url_label")}</th><th>{self._t("local_path")}</th></tr>'
                    f'{asset_trs}</table></div>'
                )

            # Content blocks table
            blocks = ContentBlocksRepository(conn).list_blocks(tid)
            blocks_section = ""
            if blocks:
                block_trs = "".join(
                    f'<tr><td>{b["order_index"]}</td>'
                    f'<td>{b["pid"]}</td>'
                    f'<td class="nowrap">{html.escape(b["block_type"])}</td>'
                    f'<td class="truncate">{html.escape((b["text"] or "")[:200])}</td></tr>'
                    for b in blocks
                )
                blocks_section = (
                    f'<h2>{self._t("content_blocks")} ({len(blocks)})</h2>'
                    f'<div class="table-wrap"><table>'
                    f'<tr><th>{self._t("order_index")}</th><th>{self._t("pid")}</th>'
                    f'<th>{self._t("block_type")}</th><th>{self._t("content")}</th></tr>'
                    f'{block_trs}</table></div>'
                )

            # Reading preview
            reading_view = self._render_thread_reading_view(tid, floors, metadata, image_width=image_width)
            width_toolbar = self._thread_image_width_toolbar(tid, img_width=image_width)

            # Floors table
            floor_trs = "".join(
                f'<tr><td>{f["floor_no"]}</td><td class="mono">{f["pid"]}</td>'
                f'<td>{html.escape(f["publisher"] or "")}</td>'
                f'<td><pre style="margin:0;font-size:12px;max-height:200px;overflow:auto">{html.escape((f["content"] or "")[:2000])}</pre></td></tr>'
                for f in floors
            )

            body = (
                f'<div class="panel">{actions_html}</div>'
                f'<h2>{self._t("archive_meta")}</h2>'
                f'<div class="table-wrap"><table>{info_trs}</table></div>'
                f'{title_rows}'
                f'{assets_section}'
                f'{blocks_section}'
                f'<h2>{self._t("reading_preview")}</h2>'
                f'<p style="color:var(--text-tertiary);font-size:13px">{self._t("reading_preview_tip")}</p>'
                f'{width_toolbar}'
                f'<p><a href="{self._url(f"/threads/{tid}/context")}">{self._t("open_context")}</a></p>'
                f'{reading_view}'
                f'<h2>{self._t("floors")}</h2>'
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("floor_no")}</th><th>{self._t("pid")}</th><th>{self._t("publisher")}</th><th>{self._t("content")}</th></tr>'
                f'{floor_trs}</table></div>'
                f'<h2>{self._t("metadata_preview")}</h2>'
                f'<p><a href="{self._url(f"/threads/{tid}/metadata")}">{self._t("open_metadata")}</a></p>'
                f'<pre style="font-size:12px;max-height:400px;overflow:auto;background:var(--bg-muted);padding:12px;border:1px solid var(--border-light)">{html.escape(self._preview(metadata_text))}</pre>'
            )
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

    def _serve_static(self, path: str) -> bool:
        """Serve React SPA static files. Returns True if handled."""
        static_dir = Path(__file__).parent / "static"
        if not static_dir.exists():
            return False

        # API and media are handled elsewhere
        if path.startswith("/api/") or path.startswith("/media/") or path.startswith("/artifacts/"):
            return False

        # Try exact file match
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"
        target = static_dir / rel
        if target.is_file():
            content_type = self._guess_static_content_type(target)
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True

        # SPA fallback: serve index.html for non-file routes
        index = static_dir / "index.html"
        if index.is_file():
            data = index.read_bytes()
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True

        return False

    def _guess_static_content_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
            ".woff": "font/woff",
        }.get(suffix, "application/octet-stream")

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
            return f'<div class="panel" style="color:var(--text-tertiary)">{html.escape(self._t("thread_not_found"))}</div>'
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

    def _parse_metadata_json(self, metadata_text: str | None) -> dict[str, object] | None:
        if not metadata_text:
            return None
        try:
            parsed = json.loads(metadata_text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    # ─── Series ───

    def _series(self) -> None:
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            repo = SeriesRepository(conn)
            series_rows = repo.list_series(limit=200)
            rows = "".join(self._render_series_tr(row) for row in series_rows)
            body = (
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("id")}</th><th>{self._t("canonical_title")}</th><th>{self._t("author")}</th>'
                f'<th>{self._t("series_key")}</th><th>{self._t("threads_label")}</th><th>{self._t("needs_review")}</th></tr>'
                f'{rows}</table></div>'
            )
            self._send(self._html_page(self._t("series"), body))
        finally:
            conn.close()

    def _render_series_tr(self, row) -> str:
        series_id = int(row["series_id"])
        thread_count = int(row["thread_count"] or 0)
        needs_review = row["needs_review"]
        return (
            f'<tr>'
            f'<td class="mono"><a href="{self._url(f"/series/{series_id}")}">{series_id}</a></td>'
            f'<td class="truncate"><a href="{self._url(f"/series/{series_id}")}">{html.escape(row["canonical_title"] or row["series_key"] or "")}</a></td>'
            f'<td>{html.escape(row["author_guess"] or "-")}</td>'
            f'<td class="mono">{html.escape(row["series_key"] or "-")}</td>'
            f'<td>{thread_count}</td>'
            f'<td>{_badge(self._t("yes"), "warn") if needs_review else _badge(self._t("no"), "ok")}</td>'
            f'</tr>'
        )

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

            # Actions
            disabled = ' disabled title="' + html.escape(self._t("cannot_delete_nonempty_series"), quote=True) + '"' if len(threads) > 0 else ""
            actions = (
                f'<div class="actions">'
                f'<form method="post" action="{self._url("/title-review/merge-series")}">{self._hidden_lang()}'
                f'<input type="hidden" name="source_series_id" value="{series_id}">'
                f'<input type="hidden" name="return_to" value="series_detail">'
                f'<input type="text" name="target_series_id" placeholder="{self._t("target_series_id")}" size="10">'
                f'<button type="submit">{self._t("merge_series")}</button></form>'
                f'<form method="post" action="{self._url("/series/delete")}">{self._hidden_lang()}'
                f'<input type="hidden" name="series_id" value="{series_id}">'
                f'<button class="danger" type="submit"{disabled}>{self._t("delete_series")}</button></form>'
                f'</div>'
            )

            # Info table
            info_rows = [
                (self._t("id"), str(series["series_id"])),
                (self._t("series_key"), html.escape(series["series_key"] or "")),
                (self._t("author"), html.escape(series["author_guess"] or "")),
                ("Creator key", html.escape(series["creator_key"] or "")),
                (self._t("aliases"), f'<pre style="margin:0;font-size:12px">{html.escape(series["aliases_json"] or "[]")}</pre>'),
                ("Alias keys", f'<pre style="margin:0;font-size:12px">{html.escape(series["alias_keys_json"] or "[]")}</pre>'),
                (self._t("needs_review"), _badge(self._t("yes"), "warn") if series["needs_review"] else _badge(self._t("no"), "ok")),
            ]
            info_trs = "".join(f'<tr><th style="width:100px">{k}</th><td>{v}</td></tr>' for k, v in info_rows)

            # Threads table
            thread_trs = "".join(
                f'<tr><td class="mono"><a href="{self._url(f"/threads/{row["tid"]}")}">{row["tid"]}</a></td>'
                f'<td class="truncate">{html.escape(row["display_title"] or row["raw_title"] or "")}</td>'
                f'<td>{html.escape(row["chapter_name"] or "")}</td>'
                f'<td>{"" if row["chapter_index"] is None else row["chapter_index"]}</td>'
                f'<td>{_status_badge(row["archive_status"])}</td>'
                f'<td class="nowrap">{html.escape(row["sync_time"] or "-")}</td></tr>'
                for row in threads
            )

            body = (
                f'<div class="panel">{actions}</div>'
                f'<h2>{self._t("archive_meta")}</h2>'
                f'<div class="table-wrap"><table>{info_trs}</table></div>'
                f'<h2>{self._t("threads")}</h2>'
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("tid")}</th><th>{self._t("title")}</th><th>{self._t("chapter")}</th>'
                f'<th>Index</th><th>{self._t("archive")}</th><th>{self._t("synced")}</th></tr>'
                f'{thread_trs}</table></div>'
            )
            self._send(self._html_page(series["canonical_title"] or f"Series {series_id}", body))
        finally:
            conn.close()

    # ─── Title Review ───

    def _title_review(self) -> None:
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            threads_repo = ThreadsRepository(conn)
            series_repo = SeriesRepository(conn)
            title_rows = "".join(self._render_title_review_card(row) for row in threads_repo.list_title_review_items(limit=100))
            series_rows = "".join(self._render_series_review_tr(row) for row in series_repo.list_series_review_items(limit=100))

            no_series = f'<tr><td colspan="6" style="color:var(--text-tertiary);text-align:center">{self._t("no_review_items")}</td></tr>'
            series_table = (
                f'<h2>{self._t("series")}</h2>'
                f'<form method="post" action="{self._url("/jobs/rebuild-series")}">{self._hidden_lang()}<button type="submit">{self._t("rebuild_series")}</button></form>'
                f'<div class="table-wrap" style="margin-top:8px"><table>'
                f'<tr><th>{self._t("id")}</th><th>{self._t("canonical_title")}</th><th>{self._t("author")}</th>'
                f'<th>{self._t("series_key")}</th><th>{self._t("threads_label")}</th><th>{self._t("action")}</th></tr>'
                f'{series_rows or no_series}</table></div>'
            )

            no_review = f'<div class="panel" style="color:var(--text-tertiary)">{self._t("no_review_items")}</div>'
            body = (
                f'<h2>{self._t("thread_titles")}</h2>'
                f'{title_rows or no_review}'
                f'{series_table}'
            )
            self._send(self._html_page(self._t("title_review"), body))
        finally:
            conn.close()

    def _render_title_review_card(self, row) -> str:
        alias_text = self._json_list_to_lines(row["title_aliases_json"])
        tags_text = self._json_list_to_lines(row["tags_json"])
        summary_title = html.escape(row["display_title"] or row["raw_title"] or "")
        return f"""
        <details class="review-card">
          <summary>
            <div><strong><a href="{self._url(f"/threads/{row["tid"]}")}">{row["tid"]}</a></strong> · {summary_title}</div>
            <div style="color:var(--text-tertiary);font-size:12px">{html.escape(row["core_title_guess"] or '')} | {html.escape(row["author_guess"] or '')} | {html.escape(row["series_key"] or '')} | conf:{row["confidence"]}</div>
          </summary>
          <div class="review-form">
            <div class="review-meta">
              <div style="color:var(--text-tertiary)">{self._t("raw_title")}</div><div style="font-size:13px">{html.escape(row["raw_title"] or "")}</div>
              <div style="color:var(--text-tertiary)">{self._t("tid")}</div><div>{row["tid"]}</div>
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
              <div class="actions" style="margin-top:10px">
                <button class="primary" type="submit">{self._t("save_title_review")}</button>
              </div>
            </form>
            <div class="actions" style="margin-top:8px">
              <form method="post" action="{self._url("/title-review/confirm-title")}">
                {self._hidden_lang()}
                <input type="hidden" name="tid" value="{row["tid"]}">
                <button type="submit">{self._t("confirm_title")}</button>
              </form>
            </div>
          </div>
        </details>
        """

    def _render_series_review_tr(self, row) -> str:
        series_id = row["series_id"]
        aliases_text = self._json_list_to_lines(row["aliases_json"])
        return (
            f'<tr>'
            f'<td class="mono"><a href="{self._url(f"/series/{series_id}")}">{series_id}</a></td>'
            f'<td class="truncate"><a href="{self._url(f"/series/{series_id}")}">{html.escape(row["canonical_title"] or row["series_key"] or "")}</a></td>'
            f'<td>{html.escape(row["author_guess"] or "-")}</td>'
            f'<td class="mono">{html.escape(row["series_key"] or "-")}</td>'
            f'<td>{row["thread_count"]}</td>'
            f'<td><div class="actions">'
            f'<form method="post" action="{self._url("/title-review/confirm-series")}">{self._hidden_lang()}<input type="hidden" name="series_id" value="{series_id}"><button type="submit">{self._t("confirm_series")}</button></form>'
            f'<form method="post" action="{self._url("/title-review/merge-series")}">{self._hidden_lang()}<input type="hidden" name="source_series_id" value="{series_id}"><input type="text" name="target_series_id" placeholder="{self._t("target_series_id")}" size="8"><button type="submit">{self._t("merge_series")}</button></form>'
            f'</div></td></tr>'
        )

    # ─── Exports ───

    def _exports(self) -> None:
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            exports = ThreadsRepository(conn).list_exports(limit=200)
            rows = "".join(self._render_export_tr(row) for row in exports)
            body = (
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("tid")}</th><th>{self._t("title")}</th><th>{self._t("archive")}</th><th>{self._t("export_path")}</th><th>{self._t("operations")}</th></tr>'
                f'{rows}</table></div>'
            )
            self._send(self._html_page(self._t("exports"), body))
        finally:
            conn.close()

    def _render_export_tr(self, row) -> str:
        tid = int(row["tid"])
        actions = (
            f'<div class="actions">'
            f'<form method="post" action="{self._url("/jobs/resync-thread")}">{self._hidden_lang()}<input type="hidden" name="tid" value="{tid}"><button type="submit">{self._t("resync_thread")}</button></form>'
            f'<form method="post" action="{self._url("/jobs/export-thread")}">{self._hidden_lang()}<input type="hidden" name="tid" value="{tid}"><input type="hidden" name="strategy" value="sync_if_stale"><button type="submit">{self._t("create_export_job")}</button></form>'
            f'</div>'
        )
        return (
            f'<tr>'
            f'<td class="mono"><a href="{self._url(f"/threads/{tid}")}">{tid}</a></td>'
            f'<td class="truncate"><a href="{self._url(f"/threads/{tid}")}">{html.escape(row["display_title"] or row["raw_title"] or "")}</a></td>'
            f'<td>{_status_badge(row["archive_status"])}</td>'
            f'<td class="truncate">{html.escape(row["export_path"] or "-")}</td>'
            f'<td>{actions}</td></tr>'
        )

    # ─── Forums ───

    def _forums(self) -> None:
        conn = connect(self.settings.db_path)
        migrate(conn)
        try:
            rows_db = conn.execute(
                """
                SELECT f.*, COUNT(t.tid) AS thread_count
                FROM forums f
                LEFT JOIN threads t ON t.forum_id = f.forum_id
                GROUP BY f.forum_id
                ORDER BY f.forum_id
                """
            ).fetchall()
            rows = "".join(self._render_forum_tr(r) for r in rows_db)
            body = (
                f'<div class="table-wrap"><table>'
                f'<tr><th>{self._t("forum_id")}</th><th>{self._t("forum_name")}</th>'
                f'<th>{self._t("content_kind")}</th><th>{self._t("threads_label")}</th>'
                f'<th>{self._t("enabled")}</th></tr>'
                f'{rows}</table></div>'
            )
            self._send(self._html_page(self._t("forums"), body))
        finally:
            conn.close()

    def _render_forum_tr(self, row) -> str:
        enabled = row["enabled"]
        return (
            f'<tr>'
            f'<td class="mono">{row["forum_id"]}</td>'
            f'<td>{html.escape(row["name"])}</td>'
            f'<td class="nowrap">{_badge(row["content_kind"], "accent")}</td>'
            f'<td>{row["thread_count"]}</td>'
            f'<td>{_badge(self._t("yes"), "ok") if enabled else _badge(self._t("no"), "muted")}</td>'
            f'</tr>'
        )

    # ─── POST Handlers ───

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
                actor="web", action="confirm_title_review", target_type="thread",
                target_id=str(tid), before=before, after=after,
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
                actor="web", action="update_title_review", target_type="thread",
                target_id=str(tid), before=before, after=after,
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
                actor="web", action="confirm_series_review", target_type="series",
                target_id=str(series_id), before=before, after=after,
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
                actor="web", action="merge_series", target_type="series",
                target_id=str(target_series_id), before=before, after=after,
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
                actor="web", action="delete_thread", target_type="thread",
                target_id=str(tid), before=before, after=after,
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
                actor="web", action="delete_series", target_type="series",
                target_id=str(series_id), before=before, after=after,
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
        )
    run(settings)


if __name__ == "__main__":
    main()
