from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from yamibo_mcp.application.discussion_discovery_queries import find_discussions
from yamibo_mcp.application.discussion_report import create_forum_research_report_job
from yamibo_mcp.application.discussion_trend_commands import create_discussion_trend_index_job
from yamibo_mcp.application.knowledge_queries import get_knowledge_research
from yamibo_mcp.server.agent_adapter import to_wire

router = APIRouter(prefix="/api", tags=["knowledge"])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscussionSearch(StrictBody):
    query: StrictStr = ""
    forum_ids: list[StrictInt] | None = Field(default=None, max_length=100)
    tids: list[StrictInt] | None = Field(default=None, max_length=25)
    start_date: StrictStr | None = Field(default=None, max_length=40)
    end_date: StrictStr | None = Field(default=None, max_length=40)
    limit: StrictInt = Field(default=10, ge=1, le=50)
    timeout_ms: StrictInt = Field(default=1500, ge=1, le=5000)
    floor_match_limit: StrictInt = Field(default=5000, ge=1, le=20000)


def _discussion_error(result):
    error = result.error
    status = {
        "INVALID_ARGUMENT": 422,
        "INVALID_FORUM_SCOPE": 422,
        "QUERY_TIMEOUT": 504,
        "QUERY_FAILED": 503,
    }.get(error.code, 500)
    raise HTTPException(status_code=status, detail={"code": error.code, "message": error.message, "retryable": error.retryable})


@router.post("/knowledge/discussions/search")
def discussion_search(body: DiscussionSearch):
    result = find_discussions(**body.model_dump())
    if not result.ok or result.data is None:
        _discussion_error(result)
    return result.data


@router.post("/knowledge/research")
def knowledge_research(body: dict):
    result = get_knowledge_research(
        question=str(body.get("question") or ""),
        forum_id=int(body["forum_id"]) if body.get("forum_id") not in {None, ""} else None,
        start_date=str(body.get("start_date") or "") or None,
        end_date=str(body.get("end_date") or "") or None,
        intent=str(body.get("intent") or "") or None,
        retrieval_mode=str(body.get("retrieval_mode") or "") or None,
    )
    return to_wire(result)


@router.post("/knowledge/trend-index")
def knowledge_trend_index(body: dict):
    result = create_discussion_trend_index_job(
        forum_id=int(body["forum_id"]),
        start_date=str(body.get("start_date") or ""),
        end_date=str(body.get("end_date") or ""),
        version=str(body.get("version") or "trend-v1"),
    )
    return to_wire(result)


@router.post("/knowledge/research-report")
def knowledge_research_report(body: dict):
    result = create_forum_research_report_job(
        forum_id=int(body["forum_id"]),
        start_date=str(body.get("start_date") or ""),
        end_date=str(body.get("end_date") or ""),
        question=str(body.get("question") or ""),
        intent=str(body.get("intent") or "general_research"),
        query=str(body.get("query") or body.get("question") or ""),
    )
    return to_wire(result)
