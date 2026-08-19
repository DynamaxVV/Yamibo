from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from .chat_runtime import ChatRun, ChatRunRegistry
from .chat_compat import HermesChatCompletionsTransport
from .hermes_api import HermesApiClient, HermesApiError, HermesAuthError, HermesNotFoundError, HermesProtocolError, HermesUnavailableError, HermesTimeoutError
from yamibo_mcp.config import read_local_config

REQUIRED_CAPABILITIES = {"run_submission", "run_status", "run_events_sse", "run_stop", "run_approval_response", "session_resources"}
CHAT_COMPLETIONS_CAPABILITIES = {"chat_completions", "chat_completion", "openai_chat_completions"}
RUNS_TRANSPORT = "hermes_runs"
CHAT_COMPLETIONS_TRANSPORT = "hermes_http"
UNAVAILABLE_TRANSPORT = "unavailable"
log = logging.getLogger(__name__)


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
        self._registry_owned = registry is None
        self.registry = registry or ChatRunRegistry(self.client)
        self._fallback_transport: HermesChatCompletionsTransport | None = None
        self._transport_mode: str | None = None
        self._clock, self._capability_cache, self._lock = clock, None, threading.RLock()
        self._probe_lock = threading.Lock()

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

    def initialize(self, *, force: bool = False) -> dict[str, Any]:
        """Probe Hermes and select the strongest supported chat transport."""
        return self.probe_capabilities(force=force)

    def probe_capabilities(self, *, force: bool = False) -> dict[str, Any]:
        with self._probe_lock:
            with self._lock:
                if not force and self._capability_cache and self._clock() - self._capability_cache[0] < 30:
                    self._apply_transport(self._capability_cache[1].get("mode"))
                    return dict(self._capability_cache[1])
            try:
                health = self.client.health()
                capability_source = "hermes"
                try:
                    capabilities = self.client.capabilities()
                except HermesApiError as exc:
                    # Hermes 0.10.x exposes health/models/chat-completions but not
                    # the newer capability discovery endpoint.  That is a missing
                    # discovery document, not a failed Hermes connection.
                    if exc.status != 404:
                        raise
                    capabilities = {}
                    capability_source = "inferred"
                models = self.client.models()
            except Exception as exc:
                raise self._map_error(exc) from exc
            available = capabilities.get("capabilities", capabilities.get("features", []))
            if isinstance(available, dict): available = [key for key, value in available.items() if value]
            available = [str(value) for value in available] if isinstance(available, list) else []
            available_set = set(available)
            runs_ready = REQUIRED_CAPABILITIES.issubset(available_set)
            chat_ready = bool(available_set & CHAT_COMPLETIONS_CAPABILITIES)
            if not runs_ready and not chat_ready:
                probe = getattr(self.client, "chat_completion", None)
                if callable(probe):
                    try:
                        probe(messages=[{"role": "user", "content": "Hello!"}], stream=False)
                        chat_ready = True
                        if "chat_completions" not in available_set:
                            available.append("chat_completions")
                    except HermesApiError as exc:
                        if exc.status not in {404, 405}:
                            raise
                    except Exception:
                        # A failed compatibility probe is represented as an
                        # unavailable transport below; the health/models result is
                        # still retained for diagnostics.
                        pass
            mode = RUNS_TRANSPORT if runs_ready else CHAT_COMPLETIONS_TRANSPORT if chat_ready else None
            log.info(
                "Hermes capability probe connected=true mode=%s source=%s capabilities=%s",
                mode or UNAVAILABLE_TRANSPORT,
                capability_source,
                sorted(set(available)),
            )
            result = {
                "ready": mode is not None,
                "mode": mode,
                "connected": True,
                "version": health.get("version"),
                "capabilities": available,
                "capabilities_source": capability_source,
                "models": models,
            }
            if mode == CHAT_COMPLETIONS_TRANSPORT:
                result["degraded"] = True
                result["warning"] = {
                    "code": "HERMES_DEGRADED_MODE",
                    "message": "Hermes Sessions/Runs 不可用，已降级到 /v1/chat/completions",
                }
            elif mode is None:
                result["error"] = {
                    "code": "HERMES_CAPABILITY_MISSING",
                    "missing": sorted(REQUIRED_CAPABILITIES - available_set),
                }
            self._apply_transport(mode)
            with self._lock: self._capability_cache = (self._clock(), result)
            return dict(result)

    def context(self, *, force: bool = False) -> dict[str, Any]:
        try: probe = self.probe_capabilities(force=force)
        except ChatServiceError as exc:
            self._apply_transport(None)
            log.warning("Hermes capability probe failed code=%s", exc.code)
            return {"ready": False, "mode": None, "transport": UNAVAILABLE_TRANSPORT, "degraded": False, "streaming_enabled": self.streaming_enabled(), "model": self.model, "hermes": {"endpoint": self.endpoint, "connected": False, "version": None, "has_api_key": bool(self.client.api_key), "capabilities": []}, "error": exc.as_dict()["error"]}
        result = {"ready": probe["ready"], "mode": probe.get("mode"), "transport": probe.get("mode") or UNAVAILABLE_TRANSPORT, "degraded": bool(probe.get("degraded")), "streaming_enabled": self.streaming_enabled(), "model": self.model, "hermes": {"endpoint": self.endpoint, "connected": True, "version": probe.get("version"), "has_api_key": bool(self.client.api_key), "capabilities": probe["capabilities"], "capabilities_source": probe.get("capabilities_source")}}
        if not probe["ready"]: result["error"] = probe["error"]
        if probe.get("warning"): result["warning"] = probe["warning"]
        return result

    def _apply_transport(self, mode: str | None) -> None:
        if mode == self._transport_mode:
            return
        previous_mode = self._transport_mode
        if not self._registry_owned:
            self._transport_mode = mode
            if mode == CHAT_COMPLETIONS_TRANSPORT and self._fallback_transport is None:
                self._fallback_transport = HermesChatCompletionsTransport(self.client, self.settings)
            return
        old_registry = self.registry
        if mode == CHAT_COMPLETIONS_TRANSPORT:
            self._fallback_transport = self._fallback_transport or HermesChatCompletionsTransport(self.client, self.settings)
            self.registry = ChatRunRegistry(self._fallback_transport)
        else:
            self.registry = ChatRunRegistry(self.client)
        self._transport_mode = mode
        if previous_mode is not None and old_registry is not self.registry:
            old_registry.shutdown()

    def _ensure_transport(self) -> None:
        if self._capability_cache is None:
            self.initialize()
        if self._transport_mode is None:
            raise ChatServiceError("HERMES_CAPABILITY_MISSING", "Hermes does not expose a supported chat transport", 503, retryable=True)

    @property
    def _session_client(self):
        return self._fallback_transport if self._transport_mode == CHAT_COMPLETIONS_TRANSPORT and self._fallback_transport is not None else self.client

    def list_sessions(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not 0 <= limit <= 200 or offset < 0: raise ChatServiceError("CHAT_INVALID_REQUEST", "invalid pagination", 400)
        self._ensure_transport()
        return self._normalize_session_page(self._call(self._session_client.list_sessions, limit=limit, offset=offset))
    def create_session(self, *, title: str | None = None) -> dict[str, Any]:
        self._ensure_transport()
        return self._normalize_session(self._call(self._session_client.create_session, title=title or "新对话", model=self.model))
    def get_session(self, session_id: str) -> dict[str, Any]:
        self._ensure_transport()
        return self._normalize_session(self._call(self._session_client.get_session, session_id))
    def update_session(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if set(patch) - {"title", "end_reason"}: raise ChatServiceError("CHAT_INVALID_REQUEST", "invalid session fields", 400)
        self._ensure_transport()
        return self._normalize_session(self._call(self._session_client.update_session, session_id, patch))
    def get_messages(self, session_id: str) -> dict[str, Any]:
        self._ensure_transport()
        return self._normalize_messages(self._call(self._session_client.get_messages, session_id))

    def delete_session(self, session_id: str) -> dict[str, Any]:
        self._ensure_transport()
        try:
            with self.registry._lock:
                active_id = self.registry._session_active_run.get(session_id)
                active = self.registry._runs.get(active_id) if active_id else None
                if active and active.status.value not in {"completed", "failed", "cancelled", "unknown"}:
                    raise ChatServiceError("CHAT_SESSION_BUSY", "该会话已有正在运行的请求", 409)
        except ChatServiceError: raise
        return self._call(self._session_client.delete_session, session_id)

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
        self._ensure_transport()
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
        self._ensure_transport()
        try: return self.registry.get(run_id)
        except KeyError:
            try:
                result = self._session_client.get_run(run_id)
                session_id = result.get("session_id")
                if session_id:
                    result["messages"] = self._normalize_messages(self._session_client.get_messages(str(session_id)))["messages"]
                return result
            except Exception as exc: raise self._map_error(exc, run=True) from exc
    def subscribe(self, run_id: str, last_event_id: int | str | None = None):
        self._ensure_transport()
        try: return self.registry.subscribe(run_id, last_event_id)
        except KeyError as exc: raise ChatServiceError("CHAT_RUN_NOT_FOUND", "Run 不存在", 404) from exc
    def stop_run(self, run_id: str) -> dict[str, Any]:
        self._ensure_transport()
        try: return self.registry.stop(run_id)
        except KeyError as exc: raise ChatServiceError("CHAT_RUN_NOT_FOUND", "Run 不存在", 404) from exc
        except Exception as exc: raise self._map_error(exc, run=True) from exc
    def approve(self, run_id: str, *, choice: str, resolve_all: bool = False) -> dict[str, Any]:
        self._ensure_transport()
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
        if isinstance(exc, HermesApiError) and exc.code == "HERMES_CAPABILITY_MISSING": return ChatServiceError("HERMES_CAPABILITY_MISSING", "Hermes does not support this operation", 409)
        if isinstance(exc, HermesApiError): return ChatServiceError("CHAT_UPSTREAM_PROTOCOL_ERROR", "Hermes request failed", 502)
        return ChatServiceError("CHAT_UPSTREAM_PROTOCOL_ERROR", "Hermes request failed", 502)


__all__ = ["ChatService", "ChatServiceError", "REQUIRED_CAPABILITIES", "CHAT_COMPLETIONS_CAPABILITIES", "RUNS_TRANSPORT", "CHAT_COMPLETIONS_TRANSPORT"]
