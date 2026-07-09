from __future__ import annotations

from http import HTTPStatus
import json

from yamibo_mcp.config import Settings
from yamibo_mcp.services.llm_client import LLMRequestError
from yamibo_mcp.services.web_chat import delete_chat_session, get_chat_context, iter_chat_turn_stream, run_chat_turn
from ._helpers import error_response, json_response, read_json_body


def handle_chat_context(handler, settings: Settings) -> None:
    json_response(handler, get_chat_context(settings))


def handle_chat_turn(handler, settings: Settings) -> None:
    body = read_json_body(handler)
    message = str(body.get("message") or "").strip()
    history = body.get("history")
    session_id = body.get("session_id")
    stream = bool(body.get("stream"))

    if not message:
        error_response(handler, "message required")
        return
    if history is not None and not isinstance(history, list):
        error_response(handler, "history must be a list")
        return

    try:
        if stream:
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("X-Accel-Buffering", "no")
            handler.end_headers()
            for event in iter_chat_turn_stream(
                settings,
                session_id=str(session_id).strip() if session_id else None,
                message=message,
                history=history if isinstance(history, list) else None,
            ):
                if event.get("type") == "done":
                    handler.wfile.write(b"data: [DONE]\n\n")
                else:
                    handler.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                handler.wfile.flush()
            return
        payload = run_chat_turn(
            settings,
            session_id=str(session_id).strip() if session_id else None,
            message=message,
            history=history if isinstance(history, list) else None,
        )
        json_response(handler, payload)
    except LLMRequestError as exc:
        error_response(handler, str(exc), HTTPStatus.BAD_GATEWAY)
    except ValueError as exc:
        error_response(handler, str(exc))


def handle_chat_session_delete(handler, settings: Settings, session_id: str) -> None:
    session_id = session_id.strip()
    if not session_id:
        error_response(handler, "session_id required")
        return
    try:
        result = delete_chat_session(settings.project_root, session_id)
        json_response(handler, result)
    except ValueError as exc:
        error_response(handler, str(exc))
