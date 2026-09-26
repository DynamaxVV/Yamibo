from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from yamibo_mcp.application.daily_brief_service import DailyBriefError, _session_owner
from yamibo_mcp.db.repositories.daily_rules import DailyRulesRepository
from yamibo_mcp.db.transaction_scope import BorrowedConnection
from yamibo_mcp.web_fastapi.deps import get_conn


router = APIRouter(prefix="/api/daily-rules", tags=["daily-briefs"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleFields(StrictModel):
    session_id: StrictStr = Field(min_length=1, max_length=128)
    forum_ids: list[StrictInt] = Field(min_length=1, max_length=50)
    timezone: StrictStr = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    execution_time: StrictStr = Field(pattern=r"^\d{2}:\d{2}$")
    preparation_deadline: StrictStr = Field(pattern=r"^\d{2}:\d{2}$")
    budget: dict[str, Any] = Field(default_factory=dict)
    enabled: StrictBool = True


class CreateRule(RuleFields):
    pass


class UpdateRule(RuleFields):
    expected_revision: StrictInt = Field(ge=1)


def _raise_error(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _owner(conn, session_id: str) -> str:
    try:
        return _session_owner(conn, session_id)
    except DailyBriefError as exc:
        _raise_error(exc.status_code, exc.code, exc.message)
    raise AssertionError("unreachable")


def _repo(conn) -> DailyRulesRepository:
    try:
        return DailyRulesRepository(BorrowedConnection(conn))
    except ValueError as exc:
        _raise_error(503, "DAILY_RULES_UNAVAILABLE", str(exc))
    raise AssertionError("unreachable")


def _rule_values(body: RuleFields) -> dict[str, Any]:
    return {
        "forum_ids": list(body.forum_ids),
        "timezone_name": body.timezone,
        "execution_time": body.execution_time,
        "preparation_deadline": body.preparation_deadline,
        "budget": body.budget,
        "enabled": body.enabled,
    }


def _validate_forums(conn, forum_ids: list[int]) -> None:
    unique_ids = list(dict.fromkeys(forum_ids))
    if len(unique_ids) != len(forum_ids) or not unique_ids or any(forum_id <= 0 for forum_id in unique_ids):
        _raise_error(422, "INVALID_DAILY_RULE_FORUMS", "论坛 ID 必须是互不重复的正整数。")
    placeholders = ",".join(f":forum_{index}" for index in range(len(unique_ids)))
    params = {f"forum_{index}": forum_id for index, forum_id in enumerate(unique_ids)}
    rows = conn.execute(
        f"SELECT forum_id, content_kind, enabled FROM forums WHERE forum_id IN ({placeholders})",
        params,
    ).fetchall()
    eligible = {
        int(row["forum_id"])
        for row in rows
        if row["content_kind"] == "discussion" and bool(row["enabled"])
    }
    invalid = [forum_id for forum_id in unique_ids if forum_id not in eligible]
    if invalid:
        _raise_error(
            422, "DAILY_RULE_FORUM_NOT_ELIGIBLE",
            f"以下论坛不存在、已停用或不是讨论论坛：{', '.join(map(str, invalid))}。",
        )


def _map_rule(rule: dict[str, Any] | None) -> dict[str, Any] | None:
    if rule is None:
        return None
    value = dict(rule)
    value["budget"] = value.pop("budget_json", value.get("budget"))
    return value


@router.get("")
def list_rules(session_id: str = Query(..., min_length=1, max_length=128), conn=Depends(get_conn)):
    owner_id = _owner(conn, session_id)
    return {"items": [_map_rule(row) for row in _repo(conn).list_rules(owner_id=owner_id)]}


@router.post("", status_code=201)
def create_rule(body: CreateRule, conn=Depends(get_conn)):
    owner_id = _owner(conn, body.session_id)
    _validate_forums(conn, body.forum_ids)
    try:
        rule = _repo(conn).create_rule(owner_id=owner_id, now=datetime.now(timezone.utc), **_rule_values(body))
        conn.commit()
        return _map_rule(rule)
    except ValueError as exc:
        conn.rollback()
        _raise_error(422, "INVALID_DAILY_RULE", str(exc))


@router.get("/{rule_id}")
def get_rule(rule_id: str, session_id: str = Query(..., min_length=1, max_length=128), conn=Depends(get_conn)):
    owner_id = _owner(conn, session_id)
    rule = _repo(conn).get_rule(rule_id, owner_id=owner_id)
    if rule is None:
        _raise_error(404, "DAILY_RULE_NOT_FOUND", "定时规则不存在或无权访问。")
    return _map_rule(rule)


@router.put("/{rule_id}")
def update_rule(rule_id: str, body: UpdateRule, conn=Depends(get_conn)):
    owner_id = _owner(conn, body.session_id)
    _validate_forums(conn, body.forum_ids)
    repo = _repo(conn)
    try:
        rule = repo.update_rule(
            rule_id=rule_id, owner_id=owner_id, expected_revision=body.expected_revision,
            now=datetime.now(timezone.utc), **_rule_values(body),
        )
        conn.commit()
        return _map_rule(rule)
    except ValueError as exc:
        conn.rollback()
        current = repo.get_rule(rule_id, owner_id=owner_id)
        if current is not None and int(current["revision"]) != body.expected_revision:
            _raise_error(409, "DAILY_RULE_REVISION_STALE", "规则已被其他操作更新，请刷新后重试。")
        if current is None:
            _raise_error(404, "DAILY_RULE_NOT_FOUND", "定时规则不存在或无权访问。")
        _raise_error(422, "INVALID_DAILY_RULE", str(exc))
