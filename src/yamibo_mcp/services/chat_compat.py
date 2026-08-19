"""Local Session/Run adapter for Hermes OpenAI-compatible chat completions.

Hermes releases that predate the Sessions/Runs API can still serve Web Chat.
This adapter keeps Yamibo's browser contract stable while storing the Session
timeline locally and translating chat-completions output into the small Run
event vocabulary understood by :mod:`chat_runtime`.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from yamibo_mcp.storage.atomic import atomic_write_text

from .hermes_api import HermesApiClient, HermesApiError, HermesNotFoundError


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{time.time_ns()}"


class LocalChatSessionStore:
    """Small atomic JSON store used only by the degraded transport."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict) and isinstance(raw.get("sessions"), list):
            return [item for item in raw["sessions"] if isinstance(item, dict)]
        return []

    def _save(self, sessions: list[dict[str, Any]]) -> None:
        atomic_write_text(self.path, json.dumps(sessions, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _summary(session: Mapping[str, Any]) -> dict[str, Any]:
        messages = session.get("messages")
        messages = messages if isinstance(messages, list) else []
        preview = None
        for message in reversed(messages):
            if isinstance(message, dict) and str(message.get("content") or "").strip():
                preview = str(message["content"])[:160]
                break
        return {
            key: session.get(key)
            for key in ("id", "title", "created_at", "updated_at")
            if session.get(key) is not None
        } | {
            "preview": preview,
            "message_count": len(messages),
            "last_active": session.get("updated_at"),
            "active_run_id": None,
        }

    def list_page(self, *, limit: int, offset: int) -> dict[str, Any]:
        with self._lock:
            sessions = list(reversed(self._load()))
        page = sessions[offset : offset + limit]
        return {
            "object": "list",
            "data": [self._summary(item) for item in page],
            "has_more": offset + limit < len(sessions),
            "limit": limit,
            "offset": offset,
        }

    def create(self, *, title: str) -> dict[str, Any]:
        now = _now_iso()
        session = {"id": _new_id("chat"), "title": title or "新对话", "created_at": now, "updated_at": now, "messages": []}
        with self._lock:
            sessions = self._load()
            sessions.append(session)
            self._save(sessions)
        return session

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            for session in self._load():
                if str(session.get("id")) == session_id:
                    return session
        raise HermesNotFoundError("session", operation="get_session")

    def update(self, session_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            sessions = self._load()
            for session in sessions:
                if str(session.get("id")) == session_id:
                    for key in ("title", "end_reason"):
                        if key in patch:
                            session[key] = patch[key]
                    session["updated_at"] = _now_iso()
                    self._save(sessions)
                    return session
        raise HermesNotFoundError("session", operation="update_session")

    def delete(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            sessions = self._load()
            next_sessions = [item for item in sessions if str(item.get("id")) != session_id]
            self._save(next_sessions)
        if len(next_sessions) == len(sessions):
            raise HermesNotFoundError("session", operation="delete_session")
        return {"ok": True, "deleted": 1}

    def messages(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        messages = session.get("messages") if isinstance(session.get("messages"), list) else []
        return {"object": "list", "session_id": session_id, "data": messages}

    def append_turn(self, session_id: str, *, prompt: str, assistant: str, raw_output: Any) -> dict[str, Any]:
        with self._lock:
            sessions = self._load()
            for session in sessions:
                if str(session.get("id")) != session_id:
                    continue
                messages = session.setdefault("messages", [])
                if not isinstance(messages, list):
                    messages = []
                    session["messages"] = messages
                now = _now_iso()
                messages.extend(
                    [
                        {"role": "user", "content": prompt, "created_at": now},
                        {"role": "assistant", "content": assistant, "raw_output": raw_output, "created_at": _now_iso()},
                    ]
                )
                session["updated_at"] = _now_iso()
                self._save(sessions)
                return session
        raise HermesNotFoundError("session", operation="append_turn")


@dataclass
class _CompatRun:
    run_id: str
    session_id: str
    input_text: str
    history: list[dict[str, Any]]
    status: str = "queued"
    assistant: str = ""
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    stop_requested: threading.Event = field(default_factory=threading.Event)
    error: dict[str, Any] | None = None
    updated_at: float = field(default_factory=time.time)


class HermesChatCompletionsTransport:
    """Expose the Runs client's duck-typed surface over chat/completions."""

    def __init__(self, client: HermesApiClient, settings: Any) -> None:
        self.client = client
        data_dir = getattr(settings, "data_dir", None)
        if data_dir is None:
            data_dir = Path(getattr(settings, "project_root", Path.cwd())) / "data"
        self.session_store = LocalChatSessionStore(Path(data_dir) / "chat" / "sessions.json")
        self._lock = threading.RLock()
        self._runs: dict[str, _CompatRun] = {}

    @property
    def endpoint(self) -> str:
        return self.client.endpoint

    @property
    def api_key(self) -> str | None:
        return self.client.api_key

    @property
    def model(self) -> str:
        return self.client.model

    def list_sessions(self, *, limit: int, offset: int, **_kwargs: Any) -> dict[str, Any]:
        return self.session_store.list_page(limit=limit, offset=offset)

    def create_session(self, *, title: str, model: str) -> dict[str, Any]:
        return {"object": "hermes.session", "session": self.session_store.create(title=title)}

    def get_session(self, session_id: str) -> dict[str, Any]:
        return {"object": "hermes.session", "session": self.session_store.get(session_id)}

    def update_session(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return {"object": "hermes.session", "session": self.session_store.update(session_id, patch)}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        return self.session_store.delete(session_id)

    def get_messages(self, session_id: str) -> dict[str, Any]:
        return self.session_store.messages(session_id)

    def start_run(self, *, session_id: str, input_text: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        self.session_store.get(session_id)
        run = _CompatRun(_new_id("compat"), session_id, input_text, list(history))
        with self._lock:
            self._runs[run.run_id] = run
        return {"run_id": run.run_id}

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise HermesNotFoundError("run", operation="get_run")
            return self._run_payload(run)

    def stop_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise HermesNotFoundError("run", operation="stop_run")
            run.stop_requested.set()
            run.updated_at = time.time()
        return {"ok": True, "run_id": run_id}

    def approve_run(self, run_id: str, *, choice: str, resolve_all: bool) -> dict[str, Any]:
        raise HermesApiError(
            "HERMES_CAPABILITY_MISSING",
            "Hermes chat/completions mode does not support Run approvals",
            status=409,
            operation="approve_run",
        )

    def iter_run_events(self, run_id: str) -> Iterator[dict[str, Any]]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise HermesNotFoundError("run", operation="iter_run_events")
            run.status = "running"
            run.updated_at = time.time()

        messages = list(run.history) + [{"role": "user", "content": run.input_text}]
        assistant_parts: list[str] = []
        try:
            try:
                for chunk in self.client.iter_chat_completion_events(messages=messages):
                    if run.stop_requested.is_set():
                        break
                    run.raw_events.append(chunk)
                    content = _extract_content(chunk)
                    if content:
                        assistant_parts.append(content)
                        yield {"json": {"type": "message.delta", "role": "assistant", "delta": content}}
                if run.stop_requested.is_set():
                    self._finish(run, "cancelled", "".join(assistant_parts))
                    yield {"json": {"type": "run.cancelled"}}
                    return
            except Exception:
                # Some OpenAI-compatible servers implement JSON completions but
                # reject stream=true.  Keep the degraded mode useful by making
                # one non-streaming request and emitting one delta.
                if assistant_parts:
                    raise
                response = self.client.chat_completion(messages=messages, stream=False)
                run.raw_events.append(response)
                content = _extract_content(response) or ""
                if content:
                    assistant_parts.append(content)
                    yield {"json": {"type": "message.delta", "role": "assistant", "delta": content}}

            assistant = "".join(assistant_parts)
            self._finish(run, "completed", assistant)
            yield {"json": {"type": "run.completed", "output": assistant}}
        except Exception as exc:
            safe_error = {"code": getattr(exc, "code", "HERMES_REQUEST_FAILED"), "message": "Hermes request failed"}
            with self._lock:
                run.status = "failed"
                run.error = safe_error
                run.updated_at = time.time()
            yield {"json": {"type": "run.failed", "error": safe_error}}

    def _finish(self, run: _CompatRun, status: str, assistant: str) -> None:
        run.assistant = assistant
        run.status = status
        run.updated_at = time.time()
        raw_output: Any = {"events": run.raw_events}
        self.session_store.append_turn(run.session_id, prompt=run.input_text, assistant=assistant, raw_output=raw_output)

    @staticmethod
    def _run_payload(run: _CompatRun) -> dict[str, Any]:
        return {
            "id": run.run_id,
            "run_id": run.run_id,
            "session_id": run.session_id,
            "status": run.status,
            "output": run.assistant,
            "error": run.error,
            "stop_requested": run.stop_requested.is_set(),
            "updated_at": run.updated_at,
        }


def _extract_content(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        value = choice.get("delta") or choice.get("message")
        if not isinstance(value, dict):
            continue
        content = value.get("content")
        if isinstance(content, str) and content:
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "".join(parts)
    return None


__all__ = ["HermesChatCompletionsTransport", "LocalChatSessionStore"]
