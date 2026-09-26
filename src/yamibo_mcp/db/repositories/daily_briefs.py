from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


class DailyBriefsRepository:
    """PostgreSQL persistence for immutable manual daily brief artifacts."""

    def __init__(self, conn):
        if getattr(conn, "backend", "postgres") not in {"postgres", "postgresql"}:
            raise ValueError("daily briefs require PostgreSQL")
        self.conn = conn

    def create_or_get_manual_issue(
        self,
        *,
        manual_issue_key: str,
        owner_id: str,
        target_day: date,
        timezone: str,
        window_start: datetime,
        window_end: datetime,
        config_snapshot: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        issue_id = str(uuid.uuid4())
        result = self.conn.execute(
            """INSERT INTO daily_issues
               (issue_id, source_kind, manual_issue_key, owner_id, target_day, timezone,
                window_start, window_end, config_snapshot_json, state)
               VALUES (:issue_id, 'manual', :manual_issue_key, :owner_id, :target_day,
                :timezone, :window_start, :window_end, CAST(:config AS JSONB), 'created')
               ON CONFLICT DO NOTHING
               RETURNING issue_id""",
            {
                "issue_id": issue_id,
                "manual_issue_key": manual_issue_key,
                "owner_id": owner_id,
                "target_day": target_day,
                "timezone": timezone,
                "window_start": window_start,
                "window_end": window_end,
                "config": _json(config_snapshot),
            },
        )
        created = result.fetchone() is not None
        issue = self.get_issue(issue_id if created else None, manual_issue_key=manual_issue_key)
        if issue is None:  # pragma: no cover - protects against unexpected isolation behavior
            raise RuntimeError("daily issue insert was not visible after conflict")
        return issue, created

    def get_issue(self, issue_id: str | None = None, *, manual_issue_key: str | None = None) -> dict[str, Any] | None:
        if issue_id is None and manual_issue_key is None:
            raise ValueError("issue_id or manual_issue_key is required")
        where, value = ("issue_id", issue_id) if issue_id is not None else ("manual_issue_key", manual_issue_key)
        row = self.conn.execute(
            f"SELECT * FROM daily_issues WHERE {where} = :value",
            {"value": value},
        ).fetchone()
        if row is None:
            return None
        issue = dict(row.items())
        issue["config_snapshot_json"] = _decode_json(issue.get("config_snapshot_json"))
        return issue

    def start_attempt(self, *, issue_id: str) -> dict[str, Any]:
        self._lock_issue(issue_id)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS attempt_no FROM daily_attempts WHERE issue_id = :issue_id",
            {"issue_id": issue_id},
        ).fetchone()
        attempt = {
            "attempt_id": str(uuid.uuid4()),
            "issue_id": issue_id,
            "attempt_no": int(row["attempt_no"]),
            "status": "running",
        }
        self.conn.execute(
            """INSERT INTO daily_attempts (attempt_id, issue_id, attempt_no, status)
               VALUES (:attempt_id, :issue_id, :attempt_no, :status)""",
            attempt,
        )
        self.conn.execute(
            "UPDATE daily_issues SET state = 'running' WHERE issue_id = :issue_id",
            {"issue_id": issue_id},
        )
        return attempt

    def finish_attempt(
        self,
        *,
        attempt_id: str,
        status: str,
        coverage: dict[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in {"succeeded", "partial", "failed"}:
            raise ValueError("attempt status must be succeeded, partial, or failed")
        result = self.conn.execute(
            """UPDATE daily_attempts SET status = :status, finished_at = CURRENT_TIMESTAMP,
                 coverage_json = CAST(:coverage AS JSONB), error_code = :error_code,
                 error_message = :error_message
               WHERE attempt_id = :attempt_id AND status = 'running'""",
            {
                "attempt_id": attempt_id,
                "status": status,
                "coverage": _json(coverage),
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        if result.rowcount != 1:
            raise ValueError("attempt is missing or already finished")
        self.conn.execute(
            "UPDATE daily_issues SET state = :status WHERE issue_id = "
            "(SELECT issue_id FROM daily_attempts WHERE attempt_id = :attempt_id)",
            {"status": status, "attempt_id": attempt_id},
        )

    def append_report_revision(
        self,
        *,
        issue_id: str,
        report: dict[str, Any],
        body_markdown: str | None,
        stats_receipt: dict[str, Any],
        source_receipts: list[dict[str, Any]],
        coverage_status: str,
        gap_reasons: list[str],
        attempt_id: str | None = None,
        regeneration_reason: str | None = None,
        status: str = "facts_ready",
    ) -> dict[str, Any]:
        self._lock_issue(issue_id)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(report_revision), 0) + 1 AS revision FROM daily_report_revisions WHERE issue_id = :issue_id",
            {"issue_id": issue_id},
        ).fetchone()
        revision = int(row["revision"])
        revision_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO daily_report_revisions
               (revision_id, issue_id, report_revision, attempt_id, status, coverage_status,
                report_json, body_markdown, stats_receipt_json, source_receipts_json,
                gap_reasons_json, regeneration_reason)
               VALUES (:revision_id, :issue_id, :revision, :attempt_id, :status,
                :coverage_status, CAST(:report AS JSONB), :body_markdown,
                CAST(:stats_receipt AS JSONB), CAST(:source_receipts AS JSONB),
                CAST(:gap_reasons AS JSONB), :regeneration_reason)""",
            {
                "revision_id": revision_id,
                "issue_id": issue_id,
                "revision": revision,
                "attempt_id": attempt_id,
                "status": status,
                "coverage_status": coverage_status,
                "report": _json(report),
                "body_markdown": body_markdown,
                "stats_receipt": _json(stats_receipt),
                "source_receipts": _json(source_receipts),
                "gap_reasons": _json(gap_reasons),
                "regeneration_reason": regeneration_reason,
            },
        )
        self.conn.execute(
            "UPDATE daily_issues SET state = 'report_ready' WHERE issue_id = :issue_id",
            {"issue_id": issue_id},
        )
        return {"revision_id": revision_id, "issue_id": issue_id, "report_revision": revision, "status": status, "coverage_status": coverage_status}

    def list_attempts(self, issue_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM daily_attempts WHERE issue_id = :issue_id ORDER BY attempt_no",
            {"issue_id": issue_id},
        ).fetchall()
        return [{**dict(row.items()), "coverage_json": _decode_json(row.get("coverage_json"))} for row in rows]

    def set_attempt_job(self, *, attempt_id: str, job_id: str) -> None:
        result = self.conn.execute(
            "UPDATE daily_attempts SET job_id = :job_id WHERE attempt_id = :attempt_id AND job_id IS NULL",
            {"attempt_id": attempt_id, "job_id": job_id},
        )
        if result.rowcount != 1:
            raise ValueError("attempt is missing or already associated with a Job")

    def list_report_revisions(self, issue_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM daily_report_revisions WHERE issue_id = :issue_id ORDER BY report_revision",
            {"issue_id": issue_id},
        ).fetchall()
        fields = ("report_json", "stats_receipt_json", "source_receipts_json", "gap_reasons_json")
        revisions = []
        for row in rows:
            item = dict(row.items())
            for field in fields:
                item[field] = _decode_json(item.get(field))
            revisions.append(item)
        return revisions

    def get_report_revision(self, revision_id: str) -> dict[str, Any] | None:
        """Resolve an immutable revision together with its owner and frozen issue scope."""
        row = self.conn.execute(
            """SELECT r.*, i.owner_id, i.source_kind, i.target_day, i.timezone,
                      i.window_start, i.window_end, i.config_snapshot_json
               FROM daily_report_revisions r
               JOIN daily_issues i ON i.issue_id = r.issue_id
               WHERE r.revision_id = :revision_id""",
            {"revision_id": revision_id},
        ).fetchone()
        if row is None:
            return None
        revision = dict(row.items())
        fields = ("report_json", "stats_receipt_json", "source_receipts_json", "gap_reasons_json", "config_snapshot_json")
        for field in fields:
            revision[field] = _decode_json(revision.get(field))
        return revision

    def _lock_issue(self, issue_id: str) -> None:
        row = self.conn.execute(
            "SELECT issue_id FROM daily_issues WHERE issue_id = :issue_id FOR UPDATE",
            {"issue_id": issue_id},
        ).fetchone()
        if row is None:
            raise ValueError("daily issue not found")
