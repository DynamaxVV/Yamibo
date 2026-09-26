"""Manual daily brief issue commands, reads, and restart-safe Job execution."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread
from typing import Any, Callable

from yamibo_mcp.application import daily_brief_coverage
from yamibo_mcp.application.daily_brief_coverage import check_daily_brief_coverage
from yamibo_mcp.application.archive_commands import create_thread_archive_job
from yamibo_mcp.application.daily_brief_editor import DailyBriefEditor
from yamibo_mcp.application.daily_brief_rendering import render_daily_brief_markdown as _render_markdown
from yamibo_mcp.application.daily_brief_facts import build_daily_brief_facts, create_manual_daily_issue
from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import connect, transaction
from yamibo_mcp.db.repositories.assistant_evidence import AssistantEvidenceRepository
from yamibo_mcp.db.repositories.daily_briefs import DailyBriefsRepository
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.domain.job_state import new_job_id
from yamibo_mcp.daemon.heartbeat import HeartbeatPacer
from yamibo_mcp.errors import LeaseNotAcquired
from yamibo_mcp.time_utils import utc_now_iso


MAX_MANUAL_FORUMS = 50
MAX_PAGE_BUDGET = 200
MAX_SOURCE_RECEIPTS = 500
MAX_SOURCE_CHARS = 6000
MAX_COVERAGE_WAIT_SECONDS = 30 * 60
DAILY_JOB_MAX_RETRIES = 100
JOB_TYPE = "daily_brief_report"
LIVE_JOB_STATUSES = ("queued", "running", "retrying", "interrupted", "paused", "cancel_requested")


class DailyBriefError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def create_manual_issue(
    *, session_id: str, target_day: date, forum_ids: list[int], timezone_name: str = "Asia/Shanghai",
    top_n: int = 10, source_pid_limit: int = 30, statement_timeout_ms: int = 3000,
    page_budget: int = 200, archive_mode: str = "text_only", settings: Settings | None = None,
) -> dict[str, Any]:
    _validate_request(session_id, forum_ids, page_budget, archive_mode)
    settings = settings or load_settings()
    conn = connect(settings, bootstrap=False)
    try:
        with transaction(conn):
            owner_id = _session_owner(conn, session_id)
            issue, created = create_manual_daily_issue(
                conn, owner_id=owner_id, target_day=target_day, forum_ids=forum_ids,
                timezone_name=timezone_name, top_n=top_n,
                source_pid_limit=source_pid_limit, statement_timeout_ms=statement_timeout_ms,
            )
            job_id = issue.get("queued_job_id")
            if created:
                job_id = _enqueue_issue_job(
                    conn, issue_id=issue["issue_id"], owner_id=owner_id, session_id=session_id,
                )
                issue = DailyBriefsRepository(conn).get_issue(issue["issue_id"])
            elif not job_id:
                job_id = _latest_live_issue_job(conn, issue["issue_id"])
            return {"issue": issue, "created": created, "job_id": job_id, "queued": bool(job_id)}
    finally:
        conn.close()


def retry_manual_issue(
    *, issue_id: str, session_id: str, regeneration_reason: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    reason = regeneration_reason.strip() if isinstance(regeneration_reason, str) else ""
    if not reason or len(reason) > 500:
        raise DailyBriefError("INVALID_REGENERATION_REASON", "请提供 1 至 500 字的补报原因。")
    settings = settings or load_settings()
    conn = connect(settings, bootstrap=False)
    try:
        with transaction(conn):
            owner_id = _session_owner(conn, session_id)
            repo = DailyBriefsRepository(conn)
            issue = repo.get_issue(issue_id)
            if issue is None or issue["owner_id"] != owner_id:
                raise DailyBriefError("DAILY_ISSUE_NOT_FOUND", "日报期次不存在或无权访问。", 404)
            if issue.get("source_kind") != "manual":
                raise DailyBriefError("DAILY_ISSUE_RETRY_NOT_ALLOWED", "定时日报不能从手动补报入口重试。", 409)
            live = _latest_live_issue_job(conn, issue_id)
            if live:
                return {"issue": issue, "job_id": live, "queued": True, "reused": True}
            job_id = _enqueue_issue_job(
                conn, issue_id=issue_id, owner_id=owner_id, session_id=session_id,
                regeneration_reason=reason,
            )
            return {"issue": repo.get_issue(issue_id), "job_id": job_id, "queued": True, "reused": False}
    finally:
        conn.close()


def list_issues(*, session_id: str, limit: int = 20, offset: int = 0, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    conn = connect(settings, bootstrap=False)
    try:
        owner_id = _session_owner(conn, session_id)
        rows = conn.execute(
            """SELECT issue_id FROM daily_issues WHERE owner_id = :owner_id
               ORDER BY target_day DESC, created_at DESC, issue_id DESC LIMIT :limit OFFSET :offset""",
            {"owner_id": owner_id, "limit": limit, "offset": offset},
        ).fetchall()
        repo = DailyBriefsRepository(conn)
        return {"items": [_issue_summary(conn, repo, row["issue_id"]) for row in rows], "limit": limit, "offset": offset}
    finally:
        conn.close()


def get_issue(*, issue_id: str, session_id: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    conn = connect(settings, bootstrap=False)
    try:
        owner_id = _session_owner(conn, session_id)
        repo = DailyBriefsRepository(conn)
        issue = repo.get_issue(issue_id)
        if issue is None or issue["owner_id"] != owner_id:
            raise DailyBriefError("DAILY_ISSUE_NOT_FOUND", "日报期次不存在或无权访问。", 404)
        return {
            "issue": issue,
            "attempts": repo.list_attempts(issue_id),
            "revisions": repo.list_report_revisions(issue_id),
            "queued_job": _job_status(conn, issue.get("queued_job_id")),
        }
    finally:
        conn.close()


def execute_daily_brief_job(
    repo: JobsRepository,
    job,
    settings: Settings,
    *,
    coverage_checker: Callable[..., dict[str, Any]] | None = None,
    facts_builder: Callable[..., dict[str, Any]] | None = None,
    editor_factory: Callable[..., Any] | None = None,
    lease_keeper: "_JobLeaseKeeper | None" = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run one bounded coverage/facts/read/edit pass; retries append a new revision."""
    conn = repo.conn
    coverage_checker = coverage_checker or check_daily_brief_coverage
    facts_builder = facts_builder or build_daily_brief_facts
    editor_factory = editor_factory or DailyBriefEditor
    payload = job.payload if isinstance(job.payload, dict) else {}
    issue_id, owner_id = str(payload.get("issue_id") or ""), str(payload.get("owner_id") or "")
    issue_repo = DailyBriefsRepository(conn)
    issue = issue_repo.get_issue(issue_id) if issue_id else None
    if issue is None or issue.get("owner_id") != owner_id:
        raise DailyBriefError("DAILY_ISSUE_JOB_MISMATCH", "日报 Job 与期次所有者不匹配。", 409)
    config = issue.get("config_snapshot_json") or {}
    target_day = issue["target_day"]
    if isinstance(target_day, datetime):
        target_day = target_day.date()
    if isinstance(target_day, str):
        target_day = date.fromisoformat(target_day)

    coverage_error: str | None = None
    coverage, attempt_id = _coverage_for_job(conn, issue_id, job.job_id)
    if coverage is None:
        try:
            # End this handler's read transaction before remote pagination. Coverage
            # receives its own connection, so it cannot commit unrelated runner state.
            conn.rollback()
            scan_conn = connect(settings, bootstrap=False)
            try:
                coverage = coverage_checker(
                    scan_conn, issue_id=issue_id, target_day=target_day,
                    forum_ids=config.get("forum_ids"), timezone_name=issue["timezone"],
                    page_budget=min(int(config.get("page_budget", MAX_PAGE_BUDGET)), MAX_PAGE_BUDGET),
                    archive_budget=min(int(config.get("archive_budget", 100)), 100),
                    archive_mode=config.get("archive_mode", "text_only"),
                    browse_page=lease_keeper.wrap_browse(daily_brief_coverage.browse_forum_page)
                    if lease_keeper else None,
                    enqueue_archive=lease_keeper.wrap_enqueue(create_thread_archive_job)
                    if lease_keeper else None,
                )
                # This connection belongs to this service; commit the coverage
                # receipt and attempt before closing it.
                scan_conn.commit()
            finally:
                scan_conn.close()
        except Exception as exc:
            coverage_error = type(exc).__name__
            coverage = _finish_failed_coverage_attempt(conn, issue_id, coverage_error)
        attempt_id = coverage.get("attempt_id") if coverage else None
        if attempt_id:
            issue_repo.set_attempt_job(attempt_id=attempt_id, job_id=job.job_id)
            started = conn.execute(
                "SELECT started_at FROM daily_attempts WHERE attempt_id=:id", {"id": attempt_id}
            ).fetchone()["started_at"]
            configured_deadline = config.get("preparation_deadline") if issue.get("source_kind") == "scheduled" else None
            deadline = (
                _datetime_utc(configured_deadline)
                if configured_deadline
                else _datetime_utc(started) + timedelta(seconds=MAX_COVERAGE_WAIT_SECONDS)
            )
            coverage["coverage_deadline_at"] = deadline.isoformat()
            _save_coverage(conn, attempt_id, coverage)
            conn.commit()

    coverage = _refresh_coverage_jobs(conn, coverage or {})
    waiting_job_ids = _live_candidate_jobs(conn, coverage)
    deadline = _coverage_deadline(coverage)
    now = _datetime_utc((clock or (lambda: datetime.now(timezone.utc)))())
    waiting_expired = bool(deadline and now >= deadline)
    if waiting_job_ids and not waiting_expired:
        if lease_keeper:
            lease_keeper.check()
        conn.commit()
        return {"status": "waiting_for_archive", "candidate_job_ids": waiting_job_ids,
                "coverage_deadline_at": deadline.isoformat() if deadline else None}

    if attempt_id:
        existing_revision = next(
            (item for item in issue_repo.list_report_revisions(issue_id) if item.get("attempt_id") == attempt_id),
            None,
        )
        if existing_revision is not None:
            return {
                "revision_id": existing_revision["revision_id"],
                "attempt_id": attempt_id,
                "coverage_status": existing_revision["coverage_status"],
                "status": existing_revision["status"],
                "reused_revision": True,
            }

    facts = facts_builder(
        conn, target_day=target_day, timezone_name=issue["timezone"],
        forum_ids=config.get("forum_ids"), top_n=config.get("top_n", 10),
        source_pid_limit=config.get("source_pid_limit", 30),
        statement_timeout_ms=config.get("statement_timeout_ms", 3000),
    )
    if waiting_job_ids:
        coverage.setdefault("gap_reasons", []).append(
            "资料等待截止时仍有候选归档 Job 未到终态，日报以 partial 保存。"
        )
        coverage["pending_candidate_job_ids_at_deadline"] = waiting_job_ids
    _save_coverage(conn, attempt_id, coverage)
    conn.commit()
    facts["coverage"] = _public_coverage(coverage or {}, facts.get("coverage", {}), coverage_error)
    source_receipts = _read_candidate_sources(conn, facts)
    conn.commit()
    editorial: dict[str, Any] = {"recommendations": []}
    editor_error = None
    editorial_gaps: list[str] = []
    if source_receipts:
        result = editor_factory(settings).edit
        import asyncio
        edited = asyncio.run(result(facts, source_receipts))
        editorial = edited.get("editorial", editorial)
        editor_error = edited.get("editor_error")
        editorial_gaps = list(edited.get("gaps") or [])
        if edited.get("facts"):
            facts = edited["facts"]
    else:
        editor_error = "没有已验证的本地讨论原文回执；已保存统计事实，未调用模型。"

    coverage_status = str(facts.get("coverage", {}).get("coverage_status") or "partial")
    gaps = list(facts.get("coverage", {}).get("gap_reasons") or []) + editorial_gaps
    if coverage_error:
        gaps.append("资料检查失败，已保留本地事实快照。")
    if not source_receipts:
        gaps.append("没有可供模型使用的已读取原文回执。")
    if editor_error:
        gaps.append("模型编辑未完成；事实与候选已保留。")
    report = {
        "issue_id": issue_id, "target_day": facts["target_day"], "timezone": facts["timezone"],
        "status": "partial", "facts": facts, "editorial": editorial,
        "source_receipts": source_receipts, "schema_version": "daily-report-v2",
    }
    markdown = _render_markdown(report)
    if lease_keeper:
        lease_keeper.check()
    with transaction(conn):
        if lease_keeper:
            repo.assert_lease(job.job_id, for_update=True)
        revision = issue_repo.append_report_revision(
            issue_id=issue_id, report=report, body_markdown=markdown,
            stats_receipt=facts, source_receipts=source_receipts,
            coverage_status=coverage_status if coverage_status in {"local_snapshot_only", "partial", "failed"} else "partial",
            gap_reasons=list(dict.fromkeys(gaps)), attempt_id=attempt_id,
            regeneration_reason=payload.get("regeneration_reason"), status="partial",
        )
    return {"revision_id": revision["revision_id"], "attempt_id": attempt_id, "coverage_status": coverage_status, "source_receipt_count": len(source_receipts), "status": "partial"}


def handle_daily_brief_report(repo: JobsRepository, job, worker_id: str, lease_seconds: int, settings: Settings) -> None:
    keeper = _JobLeaseKeeper(repo, job.job_id, worker_id, lease_seconds, settings)
    keeper.start()
    try:
        artifact = execute_daily_brief_job(repo, job, settings, lease_keeper=keeper)
        keeper.check()
    finally:
        keeper.stop()
    if artifact.get("status") == "waiting_for_archive":
        deferred = repo.retry_later(
            job.job_id, error_code="DAILY_BRIEF_WAITING_FOR_ARCHIVE",
            error_message="等待本期候选归档 Job 到达终态。",
            artifacts={"daily_brief": {"stage": "waiting_for_archive",
                                      "candidate_job_ids": artifact["candidate_job_ids"],
                                      "coverage_deadline_at": artifact.get("coverage_deadline_at")}},
            delay_seconds=30, max_delay_seconds=60,
        )
        if not deferred:
            repo.fail(job.job_id, "DAILY_BRIEF_WAIT_LIMIT", "日报资料等待重试预算已耗尽。")
        return
    repo.partial(job.job_id, {"daily_brief": artifact})


class _JobLeaseKeeper:
    """Refresh a job lease on a dedicated short-lived connection while work blocks."""

    def __init__(self, repo: JobsRepository, job_id: str, worker_id: str, lease_seconds: int, settings: Settings):
        self.repo = repo
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_seconds = max(1, int(lease_seconds))
        self.settings = settings
        configured_interval = getattr(settings, "worker_heartbeat_seconds", 15)
        self.interval = max(0.1, min(float(configured_interval), self.lease_seconds / 3))
        self._stop = Event()
        self._thread: Thread | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        self._beat_once()
        self._thread = Thread(target=self._run, name=f"daily-brief-lease-{self.job_id}", daemon=True)
        self._thread.start()

    def _beat_once(self) -> None:
        conn = connect(self.settings, bootstrap=False)
        try:
            heartbeat_repo = JobsRepository(conn, owner_id=self.worker_id)
            token = self.repo._lease_tokens.get(self.job_id)
            if token:
                heartbeat_repo._lease_tokens[self.job_id] = token
            pacer = HeartbeatPacer(
                repo=heartbeat_repo, job_id=self.job_id, worker_id=self.worker_id,
                lease_seconds=self.lease_seconds, min_interval_seconds=self.interval,
            )
            pacer.beat(force=True)
        finally:
            conn.close()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._beat_once()
            except Exception as exc:  # propagate lease loss to the foreground handler
                self._error = exc
                self._stop.set()
                return

    def check(self) -> None:
        if self._error is not None:
            raise self._error
        # A dead heartbeat thread without an exception means it was deliberately stopped.

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.interval + 1.0))

    def wrap_browse(self, callback: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(**kwargs):
            self.check()
            return callback(**kwargs)
        return guarded

    def wrap_enqueue(self, callback: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(*args, **kwargs):
            self.check()
            return callback(*args, **kwargs)
        return guarded


def _session_owner(conn, session_id: str) -> str:
    row = conn.execute("SELECT data FROM chat_sessions WHERE id = :id", {"id": session_id}).fetchone()
    if row is None:
        raise DailyBriefError("CHAT_SESSION_NOT_FOUND", "会话不存在。", 404)
    try:
        session = json.loads(row["data"])
    except (TypeError, ValueError):
        raise DailyBriefError("CHAT_SESSION_NOT_FOUND", "会话数据无效。", 404)
    if session.get("deleted"):
        raise DailyBriefError("CHAT_SESSION_NOT_FOUND", "会话不存在。", 404)
    return str(session.get("owner_id") or "local")


def _enqueue_issue_job(conn, *, issue_id: str, owner_id: str, session_id: str, regeneration_reason: str | None = None) -> str:
    job_id = new_job_id(JOB_TYPE)
    payload = {"issue_id": issue_id, "owner_id": owner_id, "session_id": session_id}
    if regeneration_reason:
        payload["regeneration_reason"] = regeneration_reason
    now = utc_now_iso()
    conn.execute(
        """INSERT INTO jobs (job_id, job_type, payload_json, status, max_retries, resumable, created_at, updated_at)
           VALUES (:job_id, :job_type, :payload, 'queued', :max_retries, TRUE, :now, :now)""",
        {"job_id": job_id, "job_type": JOB_TYPE, "payload": json.dumps(payload, ensure_ascii=False),
         "max_retries": DAILY_JOB_MAX_RETRIES, "now": now},
    )
    result = conn.execute(
        "UPDATE daily_issues SET queued_job_id = :job_id, state = 'queued' WHERE issue_id = :issue_id",
        {"job_id": job_id, "issue_id": issue_id},
    )
    if result.rowcount != 1:
        raise DailyBriefError("DAILY_ISSUE_NOT_FOUND", "日报期次不存在。", 404)
    return job_id


def _latest_live_issue_job(conn, issue_id: str) -> str | None:
    current = conn.execute("SELECT queued_job_id FROM daily_issues WHERE issue_id = :id", {"id": issue_id}).fetchone()
    job_id = current["queued_job_id"] if current else None
    if not job_id:
        return None
    job = conn.execute("SELECT status FROM jobs WHERE job_id = :id", {"id": job_id}).fetchone()
    return job_id if job and job["status"] in LIVE_JOB_STATUSES else None


def _job_status(conn, job_id: str | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    job = JobsRepository(conn).get(job_id)
    return {"job_id": job.job_id, "status": job.status, "job_type": job.job_type}


def _issue_summary(conn, repo: DailyBriefsRepository, issue_id: str) -> dict[str, Any]:
    issue = repo.get_issue(issue_id)
    revisions = repo.list_report_revisions(issue_id)
    return {"issue": issue, "latest_revision": revisions[-1] if revisions else None, "queued_job": _job_status(conn, issue.get("queued_job_id"))}


def _read_candidate_sources(conn, facts: dict[str, Any]) -> list[dict[str, Any]]:
    repo = AssistantEvidenceRepository(conn)
    start = datetime.fromisoformat(facts["window_start_utc"])
    end = datetime.fromisoformat(facts["window_end_utc"])
    receipts: list[dict[str, Any]] = []
    for candidate in facts.get("candidates", []):
        tid = int(candidate["tid"])
        stats_sources = list(candidate.get("source_receipts", []))
        # The original post provides context for an old thread, never day activity.
        background = conn.execute(
            "SELECT pid FROM floors WHERE tid=:tid AND floor_no=1 AND pub_time < :start AND pid IS NOT NULL ORDER BY pid LIMIT 1",
            {"tid": tid, "start": start},
        ).fetchone()
        if background is not None:
            stats_sources.append({"pid": background["pid"]})
        seen_pids: set[int] = set()
        for stats_receipt in stats_sources:
            if len(receipts) >= MAX_SOURCE_RECEIPTS:
                return receipts
            pid = int(stats_receipt["pid"])
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            floor = repo.read_floor(tid, pid)
            if floor is None:
                continue
            published = floor.get("pub_time")
            if published is None:
                continue
            published = published if isinstance(published, datetime) else datetime.fromisoformat(str(published))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published >= end:
                continue
            eligibility = conn.execute(
                """SELECT t.archive_status, f.content_kind AS forum_kind, f.enabled
                   FROM threads t JOIN forums f ON f.forum_id=t.forum_id WHERE t.tid=:tid""",
                {"tid": tid},
            ).fetchone()
            allowed_content = eligibility is not None and (
                (floor.get("content_kind") == "discussion" and eligibility["forum_kind"] == "discussion")
                or (floor.get("forum_id") == 30 and floor.get("content_kind") == "comic" and eligibility["forum_kind"] == "comic")
            )
            if not allowed_content or eligibility["archive_status"] != "complete" or not eligibility["enabled"]:
                continue
            content = str(floor.get("content") or "")
            if not content.strip():
                continue
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            receipts.append({
                "receipt_id": f"daily:{facts['target_day']}:{tid}:{pid}:{digest[:16]}",
                "tid": tid, "pid": pid, "published_at": published.isoformat(),
                "content_hash": digest, "content": content[:MAX_SOURCE_CHARS],
                "truncated": len(content) > MAX_SOURCE_CHARS,
                "source": "local_archived_floor",
                "time_role": "background" if published < start else "target_day",
                "historical_body_version_verified": False,
            })
    return receipts


def _public_coverage(coverage: dict[str, Any], fallback: dict[str, Any], error: str | None) -> dict[str, Any]:
    source = coverage or {}
    reasons = list(source.get("gap_reasons") or fallback.get("gap_reasons") or [])
    if error:
        reasons.append(f"coverage_check_error:{error}")
    return {
        **fallback, **source,
        "coverage_status": source.get("coverage_status") or "local_snapshot_only",
        "complete": False,
        "full_forum_coverage_proven": False,
        "gap_reasons": list(dict.fromkeys(reasons)),
    }


def _finish_failed_coverage_attempt(conn, issue_id: str, error_code: str) -> dict[str, Any]:
    repo = DailyBriefsRepository(conn)
    row = conn.execute(
        "SELECT attempt_id FROM daily_attempts WHERE issue_id=:issue AND status='running' ORDER BY attempt_no DESC LIMIT 1",
        {"issue": issue_id},
    ).fetchone()
    attempt = {"attempt_id": row["attempt_id"]} if row else repo.start_attempt(issue_id=issue_id)
    receipt = {"coverage_status": "failed", "complete": False, "full_forum_coverage_proven": False, "gap_reasons": ["coverage_check_failed"]}
    repo.finish_attempt(attempt_id=attempt["attempt_id"], status="failed", coverage=receipt, error_code="COVERAGE_CHECK_FAILED", error_message=error_code)
    return {"attempt_id": attempt["attempt_id"], **receipt}


def _coverage_for_job(conn, issue_id: str, job_id: str) -> tuple[dict[str, Any] | None, str | None]:
    row = conn.execute(
        """SELECT attempt_id, coverage_json FROM daily_attempts
           WHERE issue_id=:issue AND job_id=:job AND coverage_json IS NOT NULL
           ORDER BY attempt_no DESC LIMIT 1""",
        {"issue": issue_id, "job": job_id},
    ).fetchone()
    if row is None:
        # Recover the narrow crash window after the coverage receipt committed
        # but before this Job ID was attached to the attempt.
        row = conn.execute(
            """SELECT a.attempt_id, a.coverage_json FROM daily_attempts a
               JOIN daily_issues i ON i.issue_id=a.issue_id
               WHERE a.issue_id=:issue AND a.job_id IS NULL AND a.coverage_json IS NOT NULL
                 AND i.queued_job_id=:job
               ORDER BY a.attempt_no DESC LIMIT 1""",
            {"issue": issue_id, "job": job_id},
        ).fetchone()
        if row is not None:
            DailyBriefsRepository(conn).set_attempt_job(attempt_id=row["attempt_id"], job_id=job_id)
    if row is None:
        return None, None
    from yamibo_mcp.db.repositories.daily_briefs import _decode_json
    return _decode_json(row["coverage_json"]), row["attempt_id"]


def _save_coverage(conn, attempt_id: str | None, coverage: dict[str, Any]) -> None:
    if not attempt_id:
        return
    conn.execute(
        "UPDATE daily_attempts SET coverage_json=CAST(:coverage AS JSONB) WHERE attempt_id=:attempt",
        {"coverage": json.dumps(coverage, ensure_ascii=False, sort_keys=True, default=str), "attempt": attempt_id},
    )


def _refresh_coverage_jobs(conn, coverage: dict[str, Any]) -> dict[str, Any]:
    from yamibo_mcp.application.daily_brief_coverage import _archive_job_page_evidence, _thread_floor_evidence

    receipt = dict(coverage)
    if not receipt.get("target_day_start_utc") or not receipt.get("target_day_end_utc"):
        return receipt
    start = datetime.fromisoformat(str(receipt["target_day_start_utc"]))
    end = datetime.fromisoformat(str(receipt["target_day_end_utc"]))
    jobs = JobsRepository(conn)
    refreshed = []
    for candidate in receipt.get("candidates", []):
        entry = dict(candidate)
        archive = dict(entry.get("archive") or {})
        job_id = archive.get("job_id")
        if job_id:
            job = jobs.get(str(job_id))
            status = job.status
            terminal = status in {"succeeded", "partial", "failed", "cancelled", "dead_letter"}
            page_evidence = _archive_job_page_evidence(job.artifacts if isinstance(job.artifacts, dict) else {})
            integrity = _thread_floor_evidence(conn, int(entry["tid"]), start, end) if terminal else {}
            valid = bool(
                terminal and status == "succeeded" and page_evidence["complete"]
                and integrity.get("eligible_discussion")
                and integrity.get("archive_status") == "complete"
                and integrity.get("local_reply_count_consistent") is True
                and integrity.get("target_day_floor_count", 0) > 0
            )
            archive.update({
                "job_status": status, "job_terminal": terminal,
                "job_page_evidence": page_evidence,
                "single_thread_integrity": integrity,
                "target_day_floor_evidence_valid": valid,
                "status": "verified" if valid else "not_verifiable" if terminal and not page_evidence["complete"] else "pending_or_invalid",
            })
        entry["archive"] = archive
        refreshed.append(entry)
    receipt["candidates"] = refreshed
    gaps = [reason for reason in receipt.get("gap_reasons", []) if "candidate archive jobs" not in reason and "candidate jobs lack" not in reason]
    if any((candidate.get("archive") or {}).get("status") == "not_verifiable" for candidate in refreshed):
        gaps.append("one or more candidate jobs lack page-count and terminal-page artifacts proving archive completeness")
    if any((candidate.get("archive") or {}).get("status") == "pending_or_invalid" for candidate in refreshed):
        gaps.append("one or more candidate archive jobs ended without verified local floor completeness")
    if any((candidate.get("archive") or {}).get("status") == "enqueue_or_verify_error" for candidate in refreshed):
        gaps.append("one or more candidate archive jobs could not be inspected")
    receipt["gap_reasons"] = list(dict.fromkeys(gaps))
    receipt["complete"] = False
    receipt["full_forum_coverage_proven"] = False
    return receipt


def _live_candidate_jobs(conn, coverage: dict[str, Any]) -> list[str]:
    pending = []
    for candidate in coverage.get("candidates", []):
        archive = candidate.get("archive") or {}
        job_id = archive.get("job_id")
        if not job_id:
            continue
        row = conn.execute("SELECT status FROM jobs WHERE job_id=:id", {"id": job_id}).fetchone()
        if row is not None and row["status"] in LIVE_JOB_STATUSES:
            pending.append(str(job_id))
    return pending


def _coverage_deadline(coverage: dict[str, Any]) -> datetime | None:
    value = coverage.get("coverage_deadline_at")
    if not value:
        return None
    try:
        return _datetime_utc(value)
    except (TypeError, ValueError):
        return None


def _datetime_utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)



def _validate_request(session_id: str, forum_ids: list[int], page_budget: int, archive_mode: str) -> None:
    if not session_id or len(session_id) > 128:
        raise DailyBriefError("INVALID_SESSION_ID", "session_id 无效。")
    if not isinstance(forum_ids, list) or not forum_ids or len(forum_ids) > MAX_MANUAL_FORUMS:
        raise DailyBriefError("INVALID_FORUM_IDS", f"请选择 1 至 {MAX_MANUAL_FORUMS} 个讨论板块。")
    if any(type(value) is not int or value <= 0 for value in forum_ids) or len(set(forum_ids)) != len(forum_ids):
        raise DailyBriefError("INVALID_FORUM_IDS", "forum_ids 必须是互不重复的正整数。")
    if type(page_budget) is not int or not 1 <= page_budget <= MAX_PAGE_BUDGET:
        raise DailyBriefError("INVALID_PAGE_BUDGET", f"page_budget 必须在 1 至 {MAX_PAGE_BUDGET} 之间。")
    if archive_mode not in {"text_only", "full"}:
        raise DailyBriefError("INVALID_ARCHIVE_MODE", "archive_mode 必须是 text_only 或 full。")
