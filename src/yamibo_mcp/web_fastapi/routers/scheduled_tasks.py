"""Single-user management API for registered scheduled actions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from yamibo_mcp.db.repositories.scheduled_tasks import ScheduledTasksRepository
from yamibo_mcp.application.scheduled_task_scheduler import trigger_task
from yamibo_mcp.web_fastapi.deps import get_conn


router = APIRouter(prefix="/api/settings/scheduled-tasks", tags=["settings"])
OWNER_ID = "local"


class TaskFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=120)
    action: Literal["archive_thread"]
    arguments: dict[str, Any]
    schedule_kind: Literal["at", "cron"]
    timezone: StrictStr = Field(min_length=1, max_length=64)
    at: datetime | None = None
    cron: StrictStr | None = None
    enabled: StrictBool = True


class UpdateTask(TaskFields):
    expected_revision: StrictInt = Field(ge=1)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)


def _repo(conn) -> ScheduledTasksRepository:
    try:
        return ScheduledTasksRepository(conn)
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


def _fields(body: TaskFields) -> dict[str, Any]:
    return dict(owner_id=OWNER_ID, name=body.name, action=body.action,
                arguments=body.arguments, schedule_kind=body.schedule_kind,
                timezone_name=body.timezone, at=body.at, cron=body.cron,
                enabled=body.enabled, now=datetime.now(timezone.utc))


def _invalid(exc: ValueError) -> HTTPException:
    code = str(exc)
    return HTTPException(409 if "CONFLICT" in code else 422, code)


def _run(value: dict[str, Any]) -> dict[str, Any]:
    result = value.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    return {**value, "scheduled_for": value.get("scheduled_at"),
            "job_id": result.get("job_id"), "error": result.get("error_code")}


@router.get("")
def list_tasks(conn=Depends(get_conn)):
    return {"items": _repo(conn).list(owner_id=OWNER_ID)}


@router.post("", status_code=201)
def create_task(body: TaskFields, conn=Depends(get_conn)):
    try:
        task = _repo(conn).create(**_fields(body))
        conn.commit()
        return task
    except ValueError as exc:
        conn.rollback()
        raise _invalid(exc) from exc


@router.get("/{task_id}")
def get_task(task_id: str, conn=Depends(get_conn)):
    task = _repo(conn).get(task_id, owner_id=OWNER_ID)
    if task is None:
        raise HTTPException(404, "SCHEDULED_TASK_NOT_FOUND")
    return task


@router.put("/{task_id}")
def update_task(task_id: str, body: UpdateTask, conn=Depends(get_conn)):
    try:
        task = _repo(conn).update(task_id=task_id, expected_revision=body.expected_revision, **_fields(body))
        conn.commit()
        if task is None:
            raise HTTPException(404, "SCHEDULED_TASK_NOT_FOUND")
        return task
    except ValueError as exc:
        conn.rollback()
        raise _invalid(exc) from exc


@router.get("/{task_id}/runs")
def list_task_runs(task_id: str, conn=Depends(get_conn)):
    repo = _repo(conn)
    if repo.get(task_id, owner_id=OWNER_ID) is None:
        raise HTTPException(404, "SCHEDULED_TASK_NOT_FOUND")
    return {"items": [_run(value) for value in repo.occurrences(task_id, owner_id=OWNER_ID)]}


@router.post("/{task_id}/trigger")
def trigger_scheduled_task(task_id: str, body: RevisionRequest, conn=Depends(get_conn)):
    try:
        run = trigger_task(conn, task_id=task_id, owner_id=OWNER_ID,
                           expected_revision=body.expected_revision, now=datetime.now(timezone.utc))
        conn.commit()
        return _run(run)
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(404 if "missing" in str(exc) else 409 if "CONFLICT" in str(exc) else 422, str(exc)) from exc


@router.post("/{task_id}/archive")
def archive_scheduled_task(task_id: str, body: RevisionRequest, conn=Depends(get_conn)):
    repo = _repo(conn)
    task = repo.get(task_id, owner_id=OWNER_ID)
    if task is None:
        raise HTTPException(404, "SCHEDULED_TASK_NOT_FOUND")
    if not repo.delete(task_id, owner_id=OWNER_ID, expected_revision=body.expected_revision):
        conn.rollback()
        raise HTTPException(409, "REVISION_CONFLICT")
    conn.commit()
    return {"archived": True, "task_id": task_id}
