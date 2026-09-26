"""Non-evidence reads exposed to the model without loading arbitrary result bodies."""
from __future__ import annotations

from types import SimpleNamespace

from yamibo_mcp.db.connection import connect
from yamibo_mcp.server.schemas import _job_recovery


def job_status(settings, job_id):
    conn = connect(settings, bootstrap=False)
    try:
        row = conn.execute(
            """SELECT job_id, job_type, tid, status, stage, progress_current,
                      progress_total, error_code, created_at, updated_at, finished_at
               FROM jobs WHERE job_id = ?""", (job_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": {"code": "JOB_NOT_FOUND"}}
        data = dict(row.items())
        terminal = data["status"] in {"succeeded", "partial", "failed", "cancelled"}
        recovery = _job_recovery(
            SimpleNamespace(**data),
            {"recommended_poll_after_seconds": None if terminal else 5},
        )
        # Keep the shared recovery policy (especially no duplicate Jobs), while
        # disclosing that this metadata reader cannot inspect result artifacts.
        recovery["next_actions"] = [
            action for action in recovery["next_actions"] if action["tool"] != "read_job_events"
        ]
        if data["status"] == "partial":
            recovery["message"] = (
                "Job reported partial completion; result artifacts and completeness have not been checked. "
                "Report this limitation and inspect the existing Job in the UI before considering a rerun."
            )
        data.update(
            is_terminal=terminal,
            result_ready=None,
            result_verification="unavailable_in_scoped_status",
            result_verification_message=(
                "Only execution metadata was read. Artifacts, content completeness and export files were not verified. "
                "Open the existing Job in the UI to inspect its result; do not create a duplicate Job."
            ),
            recovery=recovery,
            needs_attention=data["status"] in {"partial", "failed", "cancelled", "interrupted"},
        )
        return {"ok": True, "data": data}
    finally:
        conn.close()


def daily_issue_status(settings, *, issue_id, owner_id):
    conn = connect(settings, bootstrap=False)
    try:
        row = conn.execute(
            """SELECT issue_id, state, target_day, queued_job_id FROM daily_issues
               WHERE issue_id = :issue_id AND owner_id = :owner_id""",
            {"issue_id": issue_id, "owner_id": owner_id},
        ).fetchone()
        if row is None:
            raise ValueError("DAILY_ISSUE_NOT_FOUND")
        revisions = conn.execute(
            """SELECT revision_id, report_revision, status, coverage_status
               FROM daily_report_revisions WHERE issue_id = :issue_id ORDER BY report_revision""",
            {"issue_id": issue_id},
        ).fetchall()
        return {"issue": dict(row.items()), "revisions": [dict(r.items()) for r in revisions]}
    finally:
        conn.close()
