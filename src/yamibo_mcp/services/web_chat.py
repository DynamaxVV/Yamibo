from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from .chat_runtime import ChatRun, ChatRunRegistry
from .hermes_api import HermesApiClient, HermesApiError, HermesAuthError, HermesNotFoundError, HermesProtocolError, HermesUnavailableError, HermesTimeoutError
from yamibo_mcp.config import read_local_config

REQUIRED_CAPABILITIES = {"run_submission", "run_status", "run_events_sse", "run_stop", "run_approval_response", "session_resources"}


@dataclass
class ChatServiceError(Exception):
    code: str
    message: str
    http_status: int
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)
        self.details = self.details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable, "details": self.details}}


class ChatService:
    def __init__(self, settings: Any | None = None, *, client: HermesApiClient | None = None, registry: ChatRunRegistry | None = None, clock=time.monotonic) -> None:
        self.settings = settings
        self.client = client or HermesApiClient(settings)
        self.registry = registry or ChatRunRegistry(self.client)
        self._clock, self._capability_cache, self._lock = clock, None, threading.RLock()

    @property
    def endpoint(self) -> str: return self.client.endpoint
    @property
    def model(self) -> str: return self.client.model

    def streaming_enabled(self) -> bool:
        """Return the UI preference without exposing any sensitive settings."""
        path = getattr(self.settings, "config_path", None)
        if path is None:
            return True
        try:
            value = read_local_config(path).get("ui", {}).get("chat_streaming_enabled")
            return bool(value) if value is not None else True
        except Exception:
            return True

    def probe_capabilities(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if not force and self._capability_cache and self._clock() - self._capability_cache[0] < 30:
                return dict(self._capability_cache[1])
        try:
            health = self.client.health()
            capabilities = self.client.capabilities()
            models = self.client.models()
        except Exception as exc:
            raise self._map_error(exc) from exc
        available = capabilities.get("capabilities", capabilities.get("features", []))
        if isinstance(available, dict): available = [key for key, value in available.items() if value]
        available = [str(value) for value in available] if isinstance(available, list) else []
        result = {"ready": REQUIRED_CAPABILITIES.issubset(set(available)), "connected": True, "version": health.get("version"), "capabilities": available, "models": models}
        if not result["ready"]: result["error"] = {"code": "HERMES_CAPABILITY_MISSING", "missing": sorted(REQUIRED_CAPABILITIES - set(available))}
        with self._lock: self._capability_cache = (self._clock(), result)
        return dict(result)

    def context(self, *, force: bool = False) -> dict[str, Any]:
        try: probe = self.probe_capabilities(force=force)
        except ChatServiceError as exc:
            return {"ready": False, "transport": "hermes_runs", "streaming_enabled": self.streaming_enabled(), "model": self.model, "hermes": {"endpoint": self.endpoint, "connected": False, "version": None, "has_api_key": bool(self.client.api_key), "capabilities": []}, "error": exc.as_dict()["error"]}
        result = {"ready": probe["ready"], "transport": "hermes_runs", "streaming_enabled": self.streaming_enabled(), "model": self.model, "hermes": {"endpoint": self.endpoint, "connected": True, "version": probe.get("version"), "has_api_key": bool(self.client.api_key), "capabilities": probe["capabilities"]}}
        if not probe["ready"]: result["error"] = probe["error"]
        return result

    def list_sessions(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not 0 <= limit <= 200 or offset < 0: raise ChatServiceError("CHAT_INVALID_REQUEST", "invalid pagination", 400)
        return self._normalize_session_page(self._call(self.client.list_sessions, limit=limit, offset=offset))
    def create_session(self, *, title: str | None = None) -> dict[str, Any]:
        return self._normalize_session(self._call(self.client.create_session, title=title or "新对话", model=self.model))
    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._normalize_session(self._call(self.client.get_session, session_id))
    def update_session(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if set(patch) - {"title", "end_reason"}: raise ChatServiceError("CHAT_INVALID_REQUEST", "invalid session fields", 400)
        return self._normalize_session(self._call(self.client.update_session, session_id, patch))
    def get_messages(self, session_id: str) -> dict[str, Any]:
        return self._normalize_messages(self._call(self.client.get_messages, session_id))

    def delete_session(self, session_id: str) -> dict[str, Any]:
        try:
            with self.registry._lock:
                active_id = self.registry._session_active_run.get(session_id)
                active = self.registry._runs.get(active_id) if active_id else None
                if active and active.status.value not in {"completed", "failed", "cancelled", "unknown"}:
                    raise ChatServiceError("CHAT_SESSION_BUSY", "该会话已有正在运行的请求", 409)
        except ChatServiceError: raise
        return self._call(self.client.delete_session, session_id)

    @staticmethod
    def _normalize_history(value: Any) -> list[dict[str, str]]:
        items = value.get("messages", []) if isinstance(value, dict) else value
        if not isinstance(items, list): raise ValueError("history is not a list")
        result = []
        for item in items:
            if not isinstance(item, dict): continue
            role, content = str(item.get("role") or "").strip(), item.get("content")
            if not role or content is None: continue
            if not isinstance(content, str): content = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if content is not None: result.append({"role": role, "content": content})
        return result

    def start_run(self, session_id: str, input_text: str) -> ChatRun:
        if not isinstance(input_text, str) or not input_text.strip(): raise ChatServiceError("CHAT_INVALID_REQUEST", "input required", 400)
        self.get_session(session_id)
        try: history = self._normalize_history(self.get_messages(session_id))
        except ChatServiceError as exc:
            if exc.code == "CHAT_SESSION_NOT_FOUND": raise
            raise ChatServiceError("CHAT_HISTORY_UNAVAILABLE", "无法读取会话历史", 502, retryable=True) from exc
        except Exception as exc: raise ChatServiceError("CHAT_HISTORY_UNAVAILABLE", "无法读取会话历史", 502, retryable=True) from exc
        try: return self.registry.submit(session_id=session_id, input_text=input_text, history=history)
        except RuntimeError as exc:
            if str(exc) == "CHAT_SESSION_BUSY": raise ChatServiceError("CHAT_SESSION_BUSY", "该会话已有正在运行的请求", 409) from exc
            raise self._map_error(exc) from exc
        except Exception as exc: raise self._map_error(exc) from exc

    def get_run(self, run_id: str) -> ChatRun | dict[str, Any]:
        try: return self.registry.get(run_id)
        except KeyError:
            try:
                result = self.client.get_run(run_id)
                session_id = result.get("session_id")
                if session_id:
                    result["messages"] = self._normalize_messages(self.client.get_messages(str(session_id)))["messages"]
                return result
            except Exception as exc: raise self._map_error(exc, run=True) from exc
    def subscribe(self, run_id: str, last_event_id: int | str | None = None):
        try: return self.registry.subscribe(run_id, last_event_id)
        except KeyError as exc: raise ChatServiceError("CHAT_RUN_NOT_FOUND", "Run 不存在", 404) from exc
    def stop_run(self, run_id: str) -> dict[str, Any]:
        try: return self.registry.stop(run_id)
        except KeyError as exc: raise ChatServiceError("CHAT_RUN_NOT_FOUND", "Run 不存在", 404) from exc
        except Exception as exc: raise self._map_error(exc, run=True) from exc
    def approve(self, run_id: str, *, choice: str, resolve_all: bool = False) -> dict[str, Any]:
        try: return self.registry.approve(run_id, choice=choice, resolve_all=resolve_all)
        except KeyError as exc: raise ChatServiceError("CHAT_RUN_NOT_FOUND", "Run 不存在", 404) from exc
        except (ValueError, RuntimeError) as exc:
            pending = "CONFLICT" in str(exc)
            raise ChatServiceError("CHAT_APPROVAL_NOT_PENDING" if pending else "CHAT_INVALID_REQUEST", "当前没有待处理审批" if pending else "invalid approval", 409 if pending else 400) from exc
        except Exception as exc: raise self._map_error(exc, run=True) from exc
    def shutdown(self) -> None: self.registry.shutdown()

    @staticmethod
    def _protocol_error(message: str) -> ChatServiceError:
        return ChatServiceError("CHAT_UPSTREAM_PROTOCOL_ERROR", message, 502)

    @classmethod
    def _normalize_session_page(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("object") != "list":
            raise cls._protocol_error("Hermes returned an invalid session list envelope")
        data, has_more, limit, offset = value.get("data"), value.get("has_more"), value.get("limit"), value.get("offset")
        if (
            not isinstance(data, list)
            or not isinstance(has_more, bool)
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or any(not isinstance(item, dict) for item in data)
        ):
            raise cls._protocol_error("Hermes returned an invalid session list payload")
        return {"sessions": data, "items": data, "has_more": has_more, "limit": limit, "offset": offset}

    @classmethod
    def _normalize_session(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("object") != "hermes.session" or not isinstance(value.get("session"), dict):
            raise cls._protocol_error("Hermes returned an invalid session envelope")
        return value["session"]

    @classmethod
    def _normalize_messages(cls, value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, dict)
            or value.get("object") != "list"
            or not isinstance(value.get("session_id"), str)
            or not isinstance(value.get("data"), list)
            or any(not isinstance(item, dict) for item in value["data"])
        ):
            raise cls._protocol_error("Hermes returned an invalid messages envelope")
        return {"session_id": value["session_id"], "messages": value["data"], "list": value["data"]}

    def _call(self, fn, *args, **kwargs):
        try: return fn(*args, **kwargs)
        except Exception as exc: raise self._map_error(exc) from exc

    @staticmethod
    def _map_error(exc: Exception, run: bool = False) -> ChatServiceError:
        if isinstance(exc, ChatServiceError): return exc
        if isinstance(exc, HermesAuthError): return ChatServiceError("HERMES_AUTH_FAILED", "Hermes authentication failed", 502)
        if isinstance(exc, HermesNotFoundError): return ChatServiceError("CHAT_RUN_NOT_FOUND" if run or "run" in str(exc).lower() else "CHAT_SESSION_NOT_FOUND", "Run 不存在" if run else "会话不存在", 404)
        if isinstance(exc, HermesProtocolError): return ChatServiceError("CHAT_UPSTREAM_PROTOCOL_ERROR", "Hermes returned an invalid response", 502)
        if isinstance(exc, HermesUnavailableError): return ChatServiceError("HERMES_UNAVAILABLE", "Hermes is unavailable", 503, retryable=True)
        if isinstance(exc, HermesTimeoutError): return ChatServiceError("HERMES_UNAVAILABLE", "Hermes is unavailable", 503, retryable=True)
        if isinstance(exc, HermesApiError): return ChatServiceError("CHAT_UPSTREAM_PROTOCOL_ERROR", "Hermes request failed", 502)
        return ChatServiceError("CHAT_UPSTREAM_PROTOCOL_ERROR", "Hermes request failed", 502)


__all__ = ["ChatService", "ChatServiceError", "REQUIRED_CAPABILITIES"]
