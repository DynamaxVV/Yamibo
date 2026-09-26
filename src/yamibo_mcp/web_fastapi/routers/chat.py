from __future__ import annotations
import json
from typing import Any, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from starlette.responses import StreamingResponse
from yamibo_mcp.application.assistant_evidence_queries import list_run_source_receipts
from yamibo_mcp.services.chat_runtime import ChatRun
from yamibo_mcp.services.web_chat import ChatService, ChatServiceError
from yamibo_mcp.web_fastapi.deps import get_chat_service

router = APIRouter(prefix="/api", tags=["chat"])
class Strict(BaseModel): model_config = ConfigDict(extra="forbid")
class CreateSession(Strict): title: str | None = None
class UpdateSession(Strict): title: str | None = None; end_reason: str | None = None
class StartRun(Strict):
    input: str = Field(min_length=1, max_length=64000)
    client_request_id: str | None = Field(default=None, max_length=128)
    mode: Literal["discovery", "selected"] = "discovery"
    forum_ids: list[StrictInt] | None = None
    tids: list[StrictInt] | None = None
    pids: list[StrictInt] | None = None
    start_at: str | None = None
    end_at: str | None = None
    report_revision: StrictInt | StrictStr | None = None
class Approval(Strict):
    choice: Literal["once", "session", "always", "deny"]
    resolve_all: bool = False
    approval_id: str | None = None
    plan_hash: str | None = None
def fail(exc: ChatServiceError):
    from fastapi import HTTPException
    return HTTPException(status_code=exc.http_status, detail=exc.as_dict()["error"])
def source_receipts_error(result):
    error = result.error
    status = {
        "INVALID_ARGUMENT": 422,
        "RUN_NOT_FOUND": 404,
        "SESSION_NOT_FOUND": 404,
        "RUN_SESSION_MISMATCH": 409,
        "RECEIPT_ACCESS_DENIED": 403,
        "RECEIPT_QUERY_FAILED": 503,
    }.get(error.code, 500)
    raise HTTPException(status_code=status, detail={"code": error.code, "message": error.message, "retryable": error.retryable})
def run_payload(run: ChatRun | dict[str, Any], session_id: str) -> dict[str, Any]:
    extras = {}
    if isinstance(run, dict):
        run_id, status = str(run.get("run_id") or run.get("id") or ""), run.get("status", "unknown")
        extras = {key: run[key] for key in ("mode", "scope", "scope_id", "report_revision", "scope_pending") if key in run}
    else: run_id, status = run.run_id, run.status.value
    return {"run_id": run_id, "session_id": session_id, "status": status, "events_url": f"/api/chat/runs/{run_id}/events", **extras}
def run_view(run: ChatRun) -> dict[str, Any]:
    return {"run_id": run.run_id, "session_id": run.session_id, "status": run.status.value,
            "created_at": run.created_at, "updated_at": run.updated_at, "last_seq": run.last_seq,
            "stop_requested": run.stop_requested, "terminal_payload": run.terminal_payload,
            "error": run.error, "messages": run.messages,
            "events_url": f"/api/chat/runs/{run.run_id}/events"}
@router.get("/chat/context")
def context(service: ChatService = Depends(get_chat_service)): return service.context()
@router.get("/chat/sessions")
def sessions(limit: int = Query(50, ge=0, le=200), offset: int = Query(0, ge=0), service: ChatService = Depends(get_chat_service)):
    try: return service.list_sessions(limit=limit, offset=offset)
    except ChatServiceError as exc: raise fail(exc)
@router.post("/chat/sessions", status_code=201)
def create_session(body: CreateSession, service: ChatService = Depends(get_chat_service)):
    try: return service.create_session(title=body.title)
    except ChatServiceError as exc: raise fail(exc)
@router.get("/chat/sessions/{session_id}")
def get_session(session_id: str, service: ChatService = Depends(get_chat_service)):
    try: return service.get_session(session_id)
    except ChatServiceError as exc: raise fail(exc)
@router.patch("/chat/sessions/{session_id}")
def update_session(session_id: str, body: UpdateSession, service: ChatService = Depends(get_chat_service)):
    try: return service.update_session(session_id, body.model_dump(exclude_none=True))
    except ChatServiceError as exc: raise fail(exc)
@router.delete("/chat/sessions/{session_id}")
def delete_session(session_id: str, service: ChatService = Depends(get_chat_service)):
    try: return service.delete_session(session_id)
    except ChatServiceError as exc: raise fail(exc)
@router.get("/chat/sessions/{session_id}/messages")
def messages(session_id: str, service: ChatService = Depends(get_chat_service)):
    try: return service.get_messages(session_id)
    except ChatServiceError as exc: raise fail(exc)
@router.post("/chat/sessions/{session_id}/runs", status_code=202)
def start_run(session_id: str, body: StartRun, service: ChatService = Depends(get_chat_service)):
    try:
        if hasattr(service, "store"):
            return run_payload(
                service.start_run(
                    session_id, body.input, body.client_request_id,
                    mode=body.mode, forum_ids=body.forum_ids, tids=body.tids,
                    pids=body.pids, start_at=body.start_at, end_at=body.end_at,
                    report_revision=body.report_revision,
                ), session_id,
            )
        return run_payload(service.start_run(session_id, body.input), session_id)
    except ChatServiceError as exc: raise fail(exc)
@router.get("/chat/runs/{run_id}")
def get_run(run_id: str, service: ChatService = Depends(get_chat_service)):
    try:
        result = service.get_run(run_id)
        return run_view(result) if isinstance(result, ChatRun) else {**result, "events_url": result.get("events_url", f"/api/chat/runs/{run_id}/events")}
    except ChatServiceError as exc: raise fail(exc)
@router.get("/chat/runs/{run_id}/source-receipts")
def run_source_receipts(
    run_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    result = list_run_source_receipts(run_id=run_id, limit=limit, offset=offset)
    if not result.ok or result.data is None:
        source_receipts_error(result)
    return result.data
@router.post("/chat/runs/{run_id}/stop")
def stop_run(run_id: str, service: ChatService = Depends(get_chat_service)):
    try: return service.stop_run(run_id)
    except ChatServiceError as exc: raise fail(exc)
@router.post("/chat/runs/{run_id}/approval")
def approval(run_id: str, body: Approval, service: ChatService = Depends(get_chat_service)):
    try:
        kwargs = {"choice": body.choice, "resolve_all": body.resolve_all}
        if hasattr(service, "store"):
            kwargs.update(approval_id=body.approval_id, plan_hash=body.plan_hash)
        return service.approve(run_id, **kwargs)
    except ChatServiceError as exc: raise fail(exc)
@router.get("/chat/runs/{run_id}/events")
def events(run_id: str, request: Request, last_event_id: str | None = Header(None, alias="Last-Event-ID"), service: ChatService = Depends(get_chat_service)):
    try: subscription = service.subscribe(run_id, last_event_id)
    except ChatServiceError as exc: raise fail(exc)
    def stream():
        terminal_seen = False
        lost_seen = False
        try:
            while True:
                try: event = subscription.get(timeout=15)
                except StopIteration:
                    break
                except Exception:
                    yield ": keepalive\n\n"; continue
                yield f"id: {event.seq}\nevent: {event.type}\ndata: {json.dumps(event.as_dict(), ensure_ascii=False, separators=(',', ':'))}\n\n"
                if event.type == "session.reconciled":
                    if terminal_seen or lost_seen: break
                elif event.type in {"run.completed", "run.failed", "run.cancelled", "run.limited", "run.interrupted"}:
                    terminal_seen = True
                elif event.type == "stream.error":
                    code = (event.payload.get("error") or {}).get("code")
                    if terminal_seen or lost_seen:
                        break
                    if code == "CHAT_RUN_STREAM_LOST":
                        lost_seen = True
                elif event.type == "stream.closed": break
        finally: subscription.close()
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/chat/sessions/{session_id}/runs")
def session_runs(session_id: str, service=Depends(get_chat_service)):
    if not hasattr(service, "list_runs"):
        return {"runs": []}
    try:
        service.get_session(session_id)
        return {"runs": [service.view(r) for r in service.list_runs(session_id)]}
    except ChatServiceError as exc:
        raise fail(exc)


@router.get("/chat/files")
def work_files(service=Depends(get_chat_service)):
    if not hasattr(service, "files"):
        return {"files": []}
    return {"files": service.files.listing()}


@router.get("/chat/files/{file_id}")
def work_file(file_id: str, offset: int = Query(0, ge=0), service=Depends(get_chat_service)):
    from fastapi import HTTPException
    if not hasattr(service, "files"):
        raise HTTPException(404)
    try:
        return service.files.read(file_id, offset)
    except (KeyError, ValueError, OSError):
        raise HTTPException(404, "文件不存在或不可读取")
