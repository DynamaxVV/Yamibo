from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse

from yamibo_mcp.config import Settings
from yamibo_mcp.services.web_chat import delete_chat_session, get_chat_context, iter_chat_turn_stream, run_chat_turn
from yamibo_mcp.web_fastapi.deps import get_settings

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/chat/context")
def chat_context(settings: Settings = Depends(get_settings)):
    return get_chat_context(settings)


@router.post("/chat/turn")
def chat_turn(body: dict, settings: Settings = Depends(get_settings)):
    message = str(body.get("message") or "").strip()
    history = body.get("history")
    session_id = body.get("session_id")
    stream = bool(body.get("stream"))

    if not message:
        raise HTTPException(status_code=400, detail="message required")
    if history is not None and not isinstance(history, list):
        raise HTTPException(status_code=400, detail="history must be a list")

    normalized_session_id = str(session_id).strip() if session_id else None
    normalized_history = history if isinstance(history, list) else None

    if stream:
        def _iter():
            for event in iter_chat_turn_stream(
                settings,
                session_id=normalized_session_id,
                message=message,
                history=normalized_history,
            ):
                if event.get("type") == "done":
                    yield "data: [DONE]\n\n"
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            _iter(),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return run_chat_turn(
        settings,
        session_id=normalized_session_id,
        message=message,
        history=normalized_history,
    )


@router.delete("/chat/sessions/{session_id}")
def chat_session_delete(session_id: str, settings: Settings = Depends(get_settings)):
    session_id = session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    return delete_chat_session(settings.project_root, session_id)
