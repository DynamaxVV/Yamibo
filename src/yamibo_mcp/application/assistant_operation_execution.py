from __future__ import annotations

import zipfile
import json
from pathlib import Path
from typing import Any

from yamibo_mcp.application.archive_commands import archive_thread_job, create_thread_export_job
from yamibo_mcp.application.assistant_operation_plans import OperationPlanError, _public_plan
from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import transaction
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.assistant_operation_plans import AssistantOperationPlansRepository, canonical_hash
from yamibo_mcp.db.repositories.jobs import JobsRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.errors import JobNotFound


_LIVE_JOB_STATES = {"queued", "running", "retrying", "interrupted", "paused", "cancel_requested"}
_TERMINAL_PLAN_STATES = {"completed", "partially_completed", "failed", "cancelled"}
_TERMINAL_ITEM_STATES = {"completed", "partially_completed", "failed", "cancelled"}


def advance_operation_plans(conn, settings) -> int:
    """Advance approved operation plans. Every Job pointer and phase receipt commits atomically."""
    changed = 0
    with transaction(conn):
        plans = AssistantOperationPlansRepository(conn)
        jobs = JobsRepository(conn)
        for candidate in plans.list_executable():
            plan = plans.get(candidate["plan_id"], lock=True)
            if plan is None or plan["status"] in _TERMINAL_PLAN_STATES:
                continue
            if plan["status"] == "approved_pending_execution":
                plans.set_status(plan["plan_id"], "active")
                plan["status"] = "active"
                changed += 1
            approved = (
                plan.get("approved_version") == plan.get("plan_version")
                and plan.get("approved_hash") == plan.get("plan_hash")
                and canonical_hash(plan["snapshot"]) == plan["plan_hash"]
            )
            if not approved:
                plans.set_status(plan["plan_id"], "failed")
                changed += 1
                continue

            for item in plan["items"]:
                if item.get("stage") in _TERMINAL_ITEM_STATES:
                    continue
                item_changed = _advance_item(
                    conn, settings, plans, jobs, plan, item,
                    cancel_requested=plan["status"] == "cancellation_requested",
                )
                if item_changed:
                    plans.save_item(plan["plan_id"], item)
                    changed += 1

            refreshed = plans.get(plan["plan_id"])
            statuses = [item.get("stage", "pending") for item in refreshed["items"]]
            if statuses and all(status in _TERMINAL_ITEM_STATES for status in statuses):
                failed = sum(status == "failed" for status in statuses)
                completed = sum(status == "completed" for status in statuses)
                cancelled = sum(status == "cancelled" for status in statuses)
                partial = sum(status == "partially_completed" for status in statuses)
                any_step_succeeded = any(
                    step.get("state") in {"satisfied", "succeeded"}
                    for item in refreshed["items"] for step in item.get("steps", [])
                )
                if partial or ((failed or cancelled) and (completed or any_step_succeeded)):
                    final_status = "partially_completed"
                elif cancelled and not failed and not completed:
                    final_status = "cancelled"
                elif failed:
                    final_status = "failed"
                elif cancelled:
                    final_status = "partially_completed" if completed else "cancelled"
                else:
                    final_status = "completed"
                plans.set_status(plan["plan_id"], final_status)
                changed += 1
    return changed


def cancel_operation_plan(*, plan_id: str, session_id: str) -> dict[str, Any]:
    """Request cancellation; already queued/running jobs remain facts and are observed to terminal."""
    settings = load_settings()
    with connect(settings.db_path) as conn:
        repo = AssistantOperationPlansRepository(conn)
        plan = repo.get(plan_id, lock=True)
        if plan is None:
            raise OperationPlanError("PLAN_NOT_FOUND", "Operation plan not found.", 404)
        row = conn.execute("SELECT data FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise OperationPlanError("SESSION_NOT_FOUND", "Session not found.", 404)
        owner_id = str(json.loads(row["data"]).get("owner_id") or "local")
        if (plan["session_id"], plan["owner_id"]) != (session_id, owner_id):
            raise OperationPlanError("PLAN_ACCESS_DENIED", "Plan does not belong to this session owner.", 403)
        if plan["status"] not in _TERMINAL_PLAN_STATES:
            if plan["status"] == "awaiting_approval":
                for item in plan["items"]:
                    for step in item.get("steps", []):
                        if step.get("state") == "required":
                            step["state"] = "cancelled"
                    item["stage"] = "cancelled"
                    repo.save_item(plan_id, item)
                repo.set_status(plan_id, "cancelled")
            else:
                repo.request_cancel(plan_id)
            plan = repo.get(plan_id)
        return _public_plan(plan)


def _advance_item(conn, settings, plans, jobs, plan, item, *, cancel_requested: bool) -> bool:
    changed = False
    tid = int(item["tid"])
    steps = item.get("steps", [])
    for index, step in enumerate(steps):
        if step.get("state") in {"satisfied", "succeeded"}:
            continue
        if step.get("state") in {"failed", "cancelled"}:
            item["stage"] = step["state"]
            return changed
        phase_key = f"{plan['plan_id']}:{tid}:{index}"
        phase = plans.get_phase(plan["plan_id"], tid, index)
        if phase is not None and phase.get("job_id"):
            try:
                job = jobs.get(str(phase["job_id"]))
            except JobNotFound as exc:  # DB reference exists but target vanished: preserve explicit failure.
                _finish_step(plans, plan, item, index, phase_key, "failed", error_code="JOB_MISSING", error_message=str(exc))
                item["stage"] = "failed"
                return True
            if job.status in _LIVE_JOB_STATES:
                step.update(state="queued", job_id=job.job_id)
                item["stage"] = "active"
                return changed
            if job.status != "succeeded":
                cancelled = job.status == "cancelled" or job.error_code == "CANCELLED"
                state = "cancelled" if cancelled else "partially_completed" if job.status == "partial" else "failed"
                _finish_step(
                    plans, plan, item, index, phase_key, state, job_id=job.job_id,
                    error_code=job.error_code or state.upper(), error_message=job.error_message,
                )
                item["stage"] = state
                return True
            if step["kind"] == "archive":
                if not _thread_archive_satisfied(ThreadsRepository(conn).get_thread(tid), str(step.get("mode", "full"))):
                    _finish_step(plans, plan, item, index, phase_key, "failed", job_id=job.job_id,
                                 error_code="ARCHIVE_NOT_COMPLETE", error_message="Archive Job succeeded but thread state is not complete at the approved capture mode.")
                    item["stage"] = "failed"
                    return True
                _finish_step(plans, plan, item, index, phase_key, "succeeded", job_id=job.job_id)
            elif step["kind"] == "export":
                valid, path, error = _validate_export(settings.data_dir, job.artifacts, str(step.get("format", "zip")))
                if not valid:
                    _finish_step(plans, plan, item, index, phase_key, "failed", job_id=job.job_id,
                                 error_code="EXPORT_FILE_INVALID", error_message=error)
                    item["stage"] = "failed"
                    return True
                _finish_step(plans, plan, item, index, phase_key, "succeeded", job_id=job.job_id, export_path=path)
            step.update(state="succeeded", job_id=job.job_id)
            changed = True
            continue

        if cancel_requested:
            _finish_step(plans, plan, item, index, phase_key, "cancelled", error_code="PLAN_CANCELLED", error_message="Plan cancellation requested before this stage started.")
            step["state"] = "cancelled"
            item["stage"] = "cancelled"
            changed = True
            continue

        if step["kind"] == "archive":
            _lock_thread_operation(conn, tid)
            if _thread_archive_satisfied(ThreadsRepository(conn).get_thread(tid), str(step.get("mode", "full"))):
                _finish_step(plans, plan, item, index, phase_key, "succeeded")
                step["state"] = "satisfied"
                changed = True
                continue
            # Queue contention, remote pause, and DB lock failures are retried on a
            # later daemon tick. Do not turn temporary infrastructure state into a
            # permanent per-item failure.
            result = archive_thread_job(tid=tid, mode=str(step.get("mode", "full")), connection=conn)
            if result["job_id"] is None:
                _finish_step(plans, plan, item, index, phase_key, "succeeded")
                step["state"] = "satisfied"
                changed = True
                continue
            _finish_step(plans, plan, item, index, phase_key, "queued", job_id=result["job_id"])
            step.update(state="queued", job_id=result["job_id"])
            item["stage"] = "active"
            return True

        if step["kind"] == "export":
            _lock_thread_operation(conn, tid)
            thread = ThreadsRepository(conn).get_thread(tid)
            if not _thread_archive_satisfied(thread, "full"):
                _finish_step(plans, plan, item, index, phase_key, "failed", error_code="FULL_ARCHIVE_REQUIRED", error_message="The approved plan has no successful full archive prerequisite.")
                step["state"] = "failed"
                item["stage"] = "failed"
                return True
            live_export = jobs.find_live_job_for_thread(job_type="export_thread", tid=tid)
            if live_export is not None:
                expected_payload = {"tid": tid, "strategy": step.get("strategy")}
                if live_export.payload != expected_payload:
                    # Let unrelated work for this TID finish before creating a
                    # different export; the daemon retries this frozen step later.
                    return False
            result = create_thread_export_job(
                tid=tid, strategy=step.get("strategy"), connection=conn,
            )
            data = result.data or {}
            job_id = str(data["job_id"])
            _finish_step(plans, plan, item, index, phase_key, "queued", job_id=job_id)
            step.update(state="queued", job_id=job_id)
            item["stage"] = "active"
            return True
        _finish_step(plans, plan, item, index, phase_key, "failed", error_code="UNKNOWN_STEP", error_message=f"Unsupported approved step kind: {step.get('kind')}")
        item["stage"] = "failed"
        return True

    if steps and all(step.get("state") in {"satisfied", "succeeded"} for step in steps):
        item["stage"] = "completed"
        return True
    if any(step.get("state") == "cancelled" for step in steps):
        item["stage"] = "cancelled"
        return True
    return changed


def _finish_step(plans, plan, item, index, phase_key, state, *, job_id=None,
                 error_code=None, error_message=None, export_path=None):
    step = item["steps"][index]
    step.update(state=state)
    if job_id:
        step["job_id"] = job_id
    if error_code:
        step["error_code"] = error_code
    if error_message:
        step["error_message"] = error_message
    if export_path:
        step["export_path"] = export_path
    plans.put_phase(
        phase_key=phase_key, plan_id=plan["plan_id"], tid=int(item["tid"]),
        step_index=index, state=state, job_id=job_id, error_code=error_code,
        error_message=error_message, export_path=export_path,
    )


def _thread_archive_satisfied(thread, mode: str) -> bool:
    if thread is None or thread.get("archive_status") != "complete":
        return False
    return mode == "text_only" or thread.get("capture_mode") == "full"


def _lock_thread_operation(conn, tid: int) -> None:
    if getattr(conn, "backend", "sqlite") == "postgres":
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(?))",
            (f"assistant_operation_thread:{tid}",),
        )


def _validate_export(data_dir, artifacts: dict[str, Any], export_format: str) -> tuple[bool, str | None, str | None]:
    raw = artifacts.get("export_path") or artifacts.get("relative_export_path")
    if not raw:
        return False, None, "Succeeded export Job has no export_path artifact."
    root = Path(data_dir).resolve()
    candidate = Path(str(raw))
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if path != root and root not in path.parents:
        return False, None, "Export path is outside the configured data directory."
    try:
        if not path.is_file():
            return False, str(path), "Export file is missing or unreadable."
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        return False, str(path), f"Export file is unreadable: {exc}"
    if export_format == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                bad_entry = archive.testzip()
            if bad_entry is not None:
                return False, str(path), f"ZIP contains a corrupt entry: {bad_entry}"
        except (OSError, zipfile.BadZipFile) as exc:
            return False, str(path), f"Export ZIP cannot be opened: {exc}"
    return True, str(path), None
