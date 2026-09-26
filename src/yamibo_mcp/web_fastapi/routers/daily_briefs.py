from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from yamibo_mcp.application.daily_brief_service import (
    DailyBriefError,
    create_manual_issue,
    get_issue,
    list_issues,
    retry_manual_issue,
)


router = APIRouter(prefix="/api/daily-issues", tags=["daily-briefs"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateManualDailyIssue(StrictModel):
    session_id: StrictStr = Field(min_length=1, max_length=128)
    target_day: date
    forum_ids: list[StrictInt] = Field(min_length=1, max_length=50)
    timezone: StrictStr = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    top_n: StrictInt = Field(default=10, ge=1, le=100)
    source_pid_limit: StrictInt = Field(default=30, ge=1, le=100)
    statement_timeout_ms: StrictInt = Field(default=3000, ge=100, le=30000)


class RetryDailyIssue(StrictModel):
    session_id: StrictStr = Field(min_length=1, max_length=128)
    regeneration_reason: StrictStr = Field(min_length=1, max_length=500)


def _raise_daily_error(exc: DailyBriefError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.post("", status_code=202)
def create_issue(body: CreateManualDailyIssue):
    try:
        return create_manual_issue(
            session_id=body.session_id,
            target_day=body.target_day,
            forum_ids=list(body.forum_ids),
            timezone_name=body.timezone,
            top_n=body.top_n,
            source_pid_limit=body.source_pid_limit,
            statement_timeout_ms=body.statement_timeout_ms,
        )
    except DailyBriefError as exc:
        _raise_daily_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_DAILY_BRIEF_REQUEST", "message": str(exc)}) from exc


@router.post("/{issue_id}/retry", status_code=202)
def retry_issue(issue_id: str, body: RetryDailyIssue):
    try:
        return retry_manual_issue(
            issue_id=issue_id, session_id=body.session_id,
            regeneration_reason=body.regeneration_reason,
        )
    except DailyBriefError as exc:
        _raise_daily_error(exc)


@router.get("")
def read_issues(
    session_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    try:
        return list_issues(session_id=session_id, limit=limit, offset=offset)
    except DailyBriefError as exc:
        _raise_daily_error(exc)


@router.get("/{issue_id}")
def read_issue(issue_id: str, session_id: str = Query(..., min_length=1, max_length=128)):
    try:
        return get_issue(issue_id=issue_id, session_id=session_id)
    except DailyBriefError as exc:
        _raise_daily_error(exc)
