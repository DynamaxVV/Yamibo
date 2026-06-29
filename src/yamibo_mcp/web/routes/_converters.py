from __future__ import annotations

from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.enums import JobStatus
from yamibo_mcp.yamibo.parsers.thread_detail import normalize_rich_body_html
from yamibo_mcp.yamibo.urls import thread_url_from_tid


JOB_TYPE_LABELS = {
    "sync_thread": "同步贴子",
    "update_thread": "追加更新贴子",
    "export_thread": "导出贴子",
    "rag_index": "构建 RAG 索引",
    "title_refine": "重算标题/系列",
    "cleanup_job": "清理任务",
    "noop": "空任务",
}

EXPORT_STRATEGY_LABELS = {
    "cache_only": "仅缓存",
    "sync_if_stale": "过期则同步",
    "force_resync": "强制重同步",
}

JOB_TYPE_LABELS_EN = {
    "sync_thread": "Sync thread",
    "update_thread": "Append update thread",
    "export_thread": "Export thread",
    "rag_index": "Build RAG index",
    "title_refine": "Rebuild titles/series",
    "cleanup_job": "Cleanup",
    "noop": "Noop",
}

EXPORT_STRATEGY_LABELS_EN = {
    "cache_only": "cache only",
    "sync_if_stale": "sync if stale",
    "force_resync": "force resync",
}

AUDIT_ACTION_LABELS = {
    "delete_series": "删除系列",
    "confirm_series_review": "确认系列",
    "merge_series": "合并系列",
    "confirm_title_review": "确认标题",
    "update_title_review": "更新标题",
    "update_series": "更新系列",
    "delete_thread": "删除贴子",
}

AUDIT_TARGET_LABELS = {
    "series": "系列",
    "thread": "贴子",
}

AUDIT_ACTION_LABELS_EN = {
    "delete_series": "Delete series",
    "confirm_series_review": "Confirm series",
    "merge_series": "Merge series",
    "confirm_title_review": "Confirm title",
    "update_title_review": "Update title",
    "update_series": "Update series",
    "delete_thread": "Delete thread",
}

AUDIT_TARGET_LABELS_EN = {
    "series": "series",
    "thread": "thread",
}

SYNC_THREAD_LIVE_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.RUNNING.value,
    JobStatus.RETRYING.value,
    JobStatus.CANCEL_REQUESTED.value,
    JobStatus.INTERRUPTED.value,
}


def clean_rich_body_html(html: str | None) -> str | None:
    if not html:
        return None
    return normalize_rich_body_html(html)


def thread_titles_by_tids(conn, tids: list[int]) -> dict[int, str]:
    unique_tids = sorted({int(tid) for tid in tids if tid is not None})
    if not unique_tids:
        return {}
    placeholders = ",".join("?" for _ in unique_tids)
    rows = conn.execute(
        f"SELECT tid, raw_title FROM threads WHERE tid IN ({placeholders})",
        unique_tids,
    ).fetchall()
    return {int(row["tid"]): (row["raw_title"] or "") for row in rows}


def job_tid(job) -> int | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    tid = payload.get("tid") or job.tid
    return None if tid is None else int(tid)


def describe_job(conn, job, thread_title: str | None = None, *, allow_thread_lookup: bool = True) -> str:
    payload = job.payload if isinstance(job.payload, dict) else {}
    tid = payload.get("tid") or job.tid

    resolved_thread_title = thread_title or ""
    if allow_thread_lookup and tid and not resolved_thread_title:
        row = ThreadsRepository(conn).get_thread(int(tid))
        if row:
            resolved_thread_title = row["raw_title"] or ""

    def _short():
        if not resolved_thread_title:
            return ""
        return (resolved_thread_title[:30] + "...") if len(resolved_thread_title) > 30 else resolved_thread_title

    if job.job_type == "sync_thread":
        desc = "同步贴子"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        return desc

    if job.job_type == "update_thread":
        desc = "追加更新贴子"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        return desc

    if job.job_type == "export_thread":
        strategy = payload.get("strategy", "")
        strategy_label = EXPORT_STRATEGY_LABELS.get(strategy, strategy)
        desc = "导出贴子"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        if strategy_label:
            desc += f"（策略：{strategy_label}）"
        return desc

    if job.job_type == "rag_index":
        desc = "构建 RAG 索引"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f"「{s}」"
        return desc

    if job.job_type == "title_refine":
        mode = payload.get("mode", "")
        if mode == "rebuild_series":
            return "批量重算系列"
        return "重算标题/系列"

    if job.job_type == "cleanup_job":
        mode = payload.get("mode", "")
        return f"清理：{mode}" if mode else "清理任务"

    return JOB_TYPE_LABELS.get(job.job_type, job.job_type)


def describe_job_en(conn, job, thread_title: str | None = None, *, allow_thread_lookup: bool = True) -> str:
    payload = job.payload if isinstance(job.payload, dict) else {}
    tid = payload.get("tid") or job.tid

    resolved_thread_title = thread_title or ""
    if allow_thread_lookup and tid and not resolved_thread_title:
        row = ThreadsRepository(conn).get_thread(int(tid))
        if row:
            resolved_thread_title = row["raw_title"] or ""

    def _short():
        if not resolved_thread_title:
            return ""
        return (resolved_thread_title[:30] + "...") if len(resolved_thread_title) > 30 else resolved_thread_title

    if job.job_type == "sync_thread":
        desc = "Sync thread"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        return desc

    if job.job_type == "update_thread":
        desc = "Append update thread"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        return desc

    if job.job_type == "export_thread":
        strategy = payload.get("strategy", "")
        strategy_label = EXPORT_STRATEGY_LABELS_EN.get(strategy, strategy)
        desc = "Export thread"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        if strategy_label:
            desc += f" ({strategy_label})"
        return desc

    if job.job_type == "rag_index":
        desc = "Build RAG index"
        if tid:
            desc += f" #{tid}"
            s = _short()
            if s:
                desc += f" \"{s}\""
        return desc

    if job.job_type == "title_refine":
        mode = payload.get("mode", "")
        if mode == "rebuild_series":
            return "Batch rebuild series"
        return "Rebuild titles/series"

    if job.job_type == "cleanup_job":
        mode = payload.get("mode", "")
        return f"Cleanup: {mode}" if mode else "Cleanup"

    return JOB_TYPE_LABELS_EN.get(job.job_type, job.job_type)


def job_failure_kind(job, *, artifacts: dict[str, object] | None = None) -> str | None:
    error_code = str(getattr(job, "error_code", "") or "").strip().lower()
    error_message = str(getattr(job, "error_message", "") or "").strip()
    failure_context = {}
    if isinstance(artifacts, dict):
        failure_context = artifacts.get("failure_context") if isinstance(artifacts.get("failure_context"), dict) else {}
    remote_fetch = failure_context.get("remote_fetch") if isinstance(failure_context, dict) and isinstance(failure_context.get("remote_fetch"), dict) else {}
    page_type = str(remote_fetch.get("page_type") or "").strip().lower() if isinstance(remote_fetch, dict) else ""
    prompt_text = str(remote_fetch.get("prompt_text") or "").strip() if isinstance(remote_fetch, dict) else ""
    combined = f"{error_code} {error_message} {page_type} {prompt_text}".lower()

    if error_code == "cancelled" or "was cancelled by user" in combined:
        return "cancelled"
    if error_code in {"local_archive_not_found", "export_precheck_failed"} or "thread archive is partial" in combined or "thread archive is not complete" in combined or "not archived locally" in combined:
        return "local_missing"
    if "content is required when no images are present" in combined:
        return "empty_content"
    if page_type == "prompt_forum_closed" or any(marker in combined for marker in ("查无此区", "此区已关闭", "版块已关闭")):
        return "forum_closed"
    if page_type == "prompt_thread_missing_or_removed_or_review" or any(marker in combined for marker in ("指定的主题不存在", "已被删除", "正在被审核")):
        return "thread_missing"
    if error_code in {"loginrequirederror", "remote_login_required"} or "login required" in combined or "login_required" in combined:
        return "login_required"
    if error_code in {"remotemaintenanceerror", "remote_maintenance"} or "maintenance" in combined:
        return "maintenance"
    if error_code in {"remotefetcherror", "remote_fetch_failed"} or "failed to read" in combined or "remote fetch" in combined:
        return "remote_fetch"
    if error_code in {"unexpectedpageerror", "unexpected_remote_page"} or "unexpected page" in combined or "expected thread detail page" in combined:
        return "unexpected_page"
    if error_code in {"invalid_argument", "valueerror"}:
        return "validation"
    return None


def job_rows_to_dicts(jobs, conn=None, include_details: bool = True) -> list[dict]:
    thread_titles_by_tid = None
    rerun_jobs_by_parent = None
    if conn is not None:
        thread_titles_by_tid = thread_titles_by_tids(conn, [job_tid(job) for job in jobs if job_tid(job) is not None])
        rerun_jobs_by_parent = JobsRepository(conn).get_latest_child_jobs(
            [job.job_id for job in jobs if job.status == "superseded"]
        )
    return [
        job_to_dict(
            job,
            conn,
            include_details=include_details,
            thread_titles_by_tid=thread_titles_by_tid,
            rerun_jobs_by_parent=rerun_jobs_by_parent,
        )
        for job in jobs
    ]


def job_to_dict(
    job,
    conn=None,
    include_details: bool = True,
    thread_titles_by_tid: dict[int, str] | None = None,
    rerun_jobs_by_parent: dict[str, object] | None = None,
) -> dict:
    payload = job.payload if isinstance(job.payload, dict) else {}
    artifacts = job.artifacts if isinstance(job.artifacts, dict) else {}
    _tid = job_tid(job)
    thread_title = thread_titles_by_tid.get(int(_tid)) if thread_titles_by_tid is not None and _tid is not None and int(_tid) in thread_titles_by_tid else None
    allow_thread_lookup = thread_titles_by_tid is None
    rerun_job = None
    if rerun_jobs_by_parent is not None and job.status == "superseded":
        rerun_job = rerun_jobs_by_parent.get(job.job_id)
    elif conn is not None and job.status == "superseded":
        rerun_job = JobsRepository(conn).get_latest_child_job(job.job_id)
    description = describe_job(conn, job, thread_title=thread_title, allow_thread_lookup=allow_thread_lookup) if conn else job.job_type
    description_en = describe_job_en(conn, job, thread_title=thread_title, allow_thread_lookup=allow_thread_lookup) if conn else job.job_type
    data = {
        "job_id": job.job_id, "job_type": job.job_type, "status": job.status,
        "stage": job.stage, "tid": job.tid,
        "description": description, "description_en": description_en,
        "progress_current": job.progress_current, "progress_total": job.progress_total,
        "worker_id": job.worker_id, "error_code": job.error_code,
        "error_message": job.error_message, "paused_at": getattr(job, "paused_at", None), "created_at": job.created_at,
        "updated_at": job.updated_at, "finished_at": job.finished_at,
        "failure_kind": job_failure_kind(job, artifacts=artifacts),
        "rerun_job_id": rerun_job.job_id if rerun_job is not None else None,
        "rerun_job_status": rerun_job.status if rerun_job is not None else None,
    }
    data["url"] = thread_url_from_tid(int(_tid)) if _tid is not None else None
    if include_details:
        data["payload"] = payload
        data["artifacts"] = artifacts
    return data


def event_to_dict(e) -> dict:
    return {
        "event_id": e.event_id, "job_id": e.job_id, "event_type": e.event_type,
        "status": e.status, "stage": e.stage, "payload": e.payload, "created_at": e.created_at,
    }


def thread_summary_dict(row) -> dict:
    def g(k):
        return row[k] if k in row.keys() else None
    return {
        "tid": int(row["tid"]), "raw_title": row["raw_title"],
        "display_title": row["display_title"] or row["raw_title"],
        "publisher": g("publisher"), "pub_time": g("pub_time"), "sync_time": g("sync_time"),
        "archive_status": g("archive_status"), "validation_status": g("validation_status"),
        "context_path": g("context_path"), "series_id": g("series_id"),
        "export_path": g("export_path"), "forum_id": g("forum_id"),
        "content_kind": g("content_kind"), "core_title_guess": g("core_title_guess"),
        "series_key": g("series_key"), "chapter_name": g("chapter_name"),
        "category": g("category"), "reply_count": g("reply_count") or 0,
    }


def floor_to_dict(row) -> dict:
    rich_body_html = row["rich_body_html"] if "rich_body_html" in row.keys() else None
    return {
        "pid": row["pid"], "floor_no": row["floor_no"],
        "publisher": row["publisher"], "content": row["content"] or "",
        "pub_time": row["pub_time"], "has_images": bool(row["has_images"]),
        "publisher_uid": row["publisher_uid"] if "publisher_uid" in row.keys() else None,
        "quote_text": row["quote_text"] if "quote_text" in row.keys() else None,
        "reply_text": row["reply_text"] if "reply_text" in row.keys() else None,
        "rich_body_html": clean_rich_body_html(rich_body_html),
    }


def asset_to_dict(row) -> dict:
    return {
        "asset_id": row["asset_id"], "tid": row["tid"], "pid": row["pid"],
        "asset_type": row["asset_type"], "remote_url": row["remote_url"],
        "local_path": row["local_path"], "exportable": bool(row["exportable"]),
        "required": bool(row["required"]), "status": row["status"],
    }


def block_to_dict(row) -> dict:
    return {
        "id": row["id"], "tid": row["tid"], "pid": row["pid"],
        "order_index": row["order_index"], "block_type": row["block_type"],
        "text": row["text"], "asset_id": row["asset_id"],
    }


def series_to_dict(row) -> dict:
    def g(k):
        return row[k] if k in row.keys() else None
    return {
        "series_id": int(row["series_id"]), "canonical_title": g("canonical_title"),
        "series_key": g("series_key"), "author_guess": g("author_guess"),
        "thread_count": g("thread_count"), "needs_review": g("needs_review"),
        "last_sync_time": g("last_sync_time"), "aliases_json": g("aliases_json"),
    }


def audit_to_dict(row, conn=None) -> dict:
    return {
        "event_id": str(row["event_id"]), "actor": row["actor"],
        "action": row["action"], "target_type": row["target_type"],
        "target_id": row["target_id"], "created_at": row["created_at"],
        "description": describe_audit(row, conn),
        "description_en": describe_audit_en(row, conn),
    }


def describe_audit(row, conn=None) -> str:
    action = row["action"]
    target_type = row["target_type"]
    target_id = row["target_id"]
    action_label = AUDIT_ACTION_LABELS.get(action, action)
    target_label = AUDIT_TARGET_LABELS.get(target_type, target_type)

    if target_type == "thread" and target_id and target_id.isdigit():
        title = ""
        if conn:
            r = ThreadsRepository(conn).get_thread(int(target_id))
            if r:
                title = r["raw_title"] or ""
        short = (title[:20] + "...") if len(title) > 20 else title
        if short:
            return f"{action_label}：{target_label} #{target_id}「{short}」"
        return f"{action_label}：{target_label} #{target_id}"

    if target_type == "series" and target_id and target_id.isdigit():
        return f"{action_label}：{target_label} #{target_id}"

    return f"{action_label}：{target_label} {target_id}"


def describe_audit_en(row, conn=None) -> str:
    action = row["action"]
    target_type = row["target_type"]
    target_id = row["target_id"]
    action_label = AUDIT_ACTION_LABELS_EN.get(action, action)
    target_label = AUDIT_TARGET_LABELS_EN.get(target_type, target_type)

    if target_type == "thread" and target_id and target_id.isdigit():
        title = ""
        if conn:
            r = ThreadsRepository(conn).get_thread(int(target_id))
            if r:
                title = r["raw_title"] or ""
        short = (title[:20] + "...") if len(title) > 20 else title
        if short:
            return f"{action_label} {target_label} #{target_id} \"{short}\""
        return f"{action_label} {target_label} #{target_id}"

    if target_type == "series" and target_id and target_id.isdigit():
        return f"{action_label} {target_label} #{target_id}"

    return f"{action_label} {target_label} {target_id}"
