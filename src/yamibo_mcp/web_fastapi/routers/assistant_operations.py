from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from yamibo_mcp.application.assistant_operation_execution import cancel_operation_plan
from yamibo_mcp.application.assistant_operation_plans import (
    OperationPlanError,
    approve_operation_plan,
    create_operation_plan,
    get_operation_plan,
    list_operation_plans,
)


router = APIRouter(prefix="/api/assistant/operation-plans", tags=["assistant-operations"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateOperationPlan(StrictModel):
    session_id: StrictStr = Field(min_length=1, max_length=128)
    run_id: StrictStr = Field(min_length=1, max_length=128)
    request_key: StrictStr = Field(min_length=1, max_length=128)
    action: StrictStr
    tids: list[StrictInt] = Field(min_length=1, max_length=25)
    require_images: StrictBool = True
    export_strategy: StrictStr | None = None


class ApproveOperationPlan(StrictModel):
    session_id: StrictStr = Field(min_length=1, max_length=128)
    plan_version: StrictInt = Field(ge=1)
    plan_hash: StrictStr = Field(min_length=64, max_length=64)


class CancelOperationPlan(StrictModel):
    session_id: StrictStr = Field(min_length=1, max_length=128)


def _raise_plan_error(exc: OperationPlanError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@router.post("", status_code=201)
def create_plan(body: CreateOperationPlan):
    try:
        return create_operation_plan(**body.model_dump())
    except OperationPlanError as exc:
        _raise_plan_error(exc)


@router.post("/{plan_id}/approve")
def approve_plan(plan_id: str, body: ApproveOperationPlan):
    try:
        return approve_operation_plan(plan_id=plan_id, **body.model_dump())
    except OperationPlanError as exc:
        _raise_plan_error(exc)


@router.post("/{plan_id}/cancel")
def cancel_plan(plan_id: str, body: CancelOperationPlan):
    try:
        return cancel_operation_plan(plan_id=plan_id, **body.model_dump())
    except OperationPlanError as exc:
        _raise_plan_error(exc)


@router.get("/{plan_id}")
def read_plan(plan_id: str, session_id: str = Query(..., min_length=1, max_length=128)):
    try:
        return get_operation_plan(plan_id=plan_id, session_id=session_id)
    except OperationPlanError as exc:
        _raise_plan_error(exc)


@router.get("")
def list_plans(
    session_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    try:
        return list_operation_plans(session_id=session_id, limit=limit, offset=offset)
    except OperationPlanError as exc:
        _raise_plan_error(exc)
