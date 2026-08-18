"""Small, synchronous client for the Hermes Runs and Sessions API.

This module deliberately has no dependency on the web framework.  It is the
single place where the server-side Hermes credential is turned into an HTTP
header and where upstream protocol errors are made safe for callers to expose.
"""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import quote, urlencode, urlsplit


CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
_APPROVAL_CHOICES = {"once", "session", "always", "deny"}


class HermesApiError(Exception):
    """A safe, stable error from the Hermes transport or protocol."""

    def __init__(self, code: str, message: str, *, status: int | None = None, operation: str | None = None) -> None:
        self.code = code
        self.status = status
        self.operation = operation
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "status": self.status, "operation": self.operation}


class HermesAuthError(HermesApiError):
    def __init__(self, *, status: int | None = None, operation: str | None = None) -> None:
        super().__init__("HERMES_AUTH_FAILED", "Hermes authentication failed", status=status, operation=operation)


class HermesNotFoundError(HermesApiError):
    def __init__(self, resource: str, *, operation: str | None = None) -> None:
        code = "HERMES_SESSION_NOT_FOUND" if resource == "session" else "HERMES_RUN_NOT_FOUND"
        super().__init__(code, f"Hermes {resource} was not found", status=404, operation=operation)


class HermesUnavailableError(HermesApiError):
    def __init__(self, *, operation: str | None = None) -> None:
        super().__init__("HERMES_UNAVAILABLE", "Hermes is unavailable", operation=operation)


class HermesTimeoutError(HermesApiError):
    def __init__(self, *, operation: str | None = None) -> None:
        super().__init__("HERMES_TIMEOUT", "Hermes request timed out", operation=operation)


class HermesProtocolError(HermesApiError):
    def __init__(self, message: str = "Hermes returned an invalid response", *, operation: str | None = None) -> None:
        super().__init__("HERMES_UPSTREAM_PROTOCOL_ERROR", message, operation=operation)


class HermesApiClient:
    """Synchronous standard-library Hermes API client."""

    def __init__(
        self,
        settings: Any | None = None,
        *,
        endpoint: str | None = None,
        host: str | None = None,
        port: int | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        if settings is not None:
            host = host or getattr(settings, "hermes_host", None)
            port = port or getattr(settings, "hermes_port", None)
            api_key = api_key if api_key is not None else getattr(settings, "hermes_api_key", None)
            model = model or getattr(settings, "hermes_model", None)
        self.endpoint = self._normalize_endpoint(endpoint, host, port)
        self.api_key = api_key
        self.model = model or "hermes-agent"

    @staticmethod
    def _normalize_endpoint(endpoint: str | None, host: str | None, port: int | None) -> str:
        value = endpoint or (f"http://{host or '127.0.0.1'}:{port or 8642}")
        parsed = urlsplit(value if "://" in value else f"http://{value}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Hermes endpoint must be an http or https URL")
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def health(self) -> dict[str, Any]:
        return self._json_request("GET", "/health", operation="health")

    def capabilities(self) -> dict[str, Any]:
        return self._json_request("GET", "/v1/capabilities", operation="capabilities")

    def models(self) -> dict[str, Any]:
        return self._json_request("GET", "/v1/models", operation="models")

    def list_sessions(
        self, *, limit: int, offset: int, source: str | None = None, include_children: bool | None = None
    ) -> dict[str, Any]:
        if not 0 <= limit <= 200 or offset < 0:
            raise ValueError("limit must be between 0 and 200 and offset must be non-negative")
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if source is not None:
            params["source"] = source
        if include_children is not None:
            params["include_children"] = str(include_children).lower()
        return self._json_request("GET", "/api/sessions", params=params, operation="list_sessions")

    def create_session(self, *, title: str, model: str) -> dict[str, Any]:
        return self._json_request("POST", "/api/sessions", body={"title": title, "model": model}, operation="create_session")

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._json_request("GET", self._session_path(session_id), operation="get_session", resource="session")

    def update_session(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("PATCH", self._session_path(session_id), body=patch, operation="update_session", resource="session")

    def delete_session(self, session_id: str) -> dict[str, Any]:
        return self._json_request("DELETE", self._session_path(session_id), operation="delete_session", resource="session")

    def get_messages(self, session_id: str) -> dict[str, Any]:
        return self._json_request("GET", self._session_path(session_id) + "/messages", operation="get_messages", resource="session")

    def start_run(self, *, session_id: str, input_text: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": self.model, "session_id": session_id, "input": input_text, "conversation_history": history}
        return self._json_request("POST", "/v1/runs", body=body, operation="start_run")

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._json_request("GET", self._run_path(run_id), operation="get_run", resource="run")

    def iter_run_events(self, run_id: str) -> Iterator[dict[str, Any]]:
        path = self._run_path(run_id) + "/events"
        response, conn = self._open("GET", path, operation="iter_run_events", accept_sse=True)
        try:
            if response.status >= 400:
                self._raise_http(response.status, "iter_run_events", resource="run")
            yield from parse_sse_events(response)
        finally:
            conn.close()

    def stop_run(self, run_id: str) -> dict[str, Any]:
        return self._json_request("POST", self._run_path(run_id) + "/stop", operation="stop_run", resource="run")

    def approve_run(self, run_id: str, *, choice: str, resolve_all: bool) -> dict[str, Any]:
        if choice not in _APPROVAL_CHOICES:
            raise ValueError("choice must be one of once, session, always, deny")
        return self._json_request("POST", self._run_path(run_id) + "/approval", body={"choice": choice, "resolve_all": resolve_all}, operation="approve_run", resource="run")

    @staticmethod
    def _session_path(session_id: str) -> str:
        return "/api/sessions/" + quote(session_id, safe="")

    @staticmethod
    def _run_path(run_id: str) -> str:
        return "/v1/runs/" + quote(run_id, safe="")

    def _json_request(self, method: str, path: str, *, body: Mapping[str, Any] | None = None, params: Mapping[str, str] | None = None, operation: str, resource: str | None = None) -> dict[str, Any]:
        if params:
            path += "?" + urlencode(params)
        response, conn = self._open(method, path, body=body, operation=operation)
        try:
            if response.status >= 400:
                self._raise_http(response.status, operation, resource=resource)
            try:
                raw_body = response.read()
            except (socket.timeout, TimeoutError) as exc:
                raise HermesTimeoutError(operation=operation) from exc
            except (OSError, ConnectionError) as exc:
                raise HermesUnavailableError(operation=operation) from exc
            if not raw_body:
                return {}
            try:
                value = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HermesProtocolError(operation=operation) from exc
            if not isinstance(value, dict):
                raise HermesProtocolError(operation=operation)
            return value
        finally:
            conn.close()

    def _open(self, method: str, path: str, *, body: Mapping[str, Any] | None = None, operation: str, accept_sse: bool = False) -> tuple[http.client.HTTPResponse, http.client.HTTPConnection]:
        parsed = urlsplit(self.endpoint)
        request_path = (parsed.path.rstrip("/") + path) or "/"
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = connection_type(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), timeout=CONNECT_TIMEOUT)
        headers = {"Accept": "text/event-stream" if accept_sse else "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if method in {"POST", "PATCH"}:
            headers["Content-Type"] = "application/json"
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            conn.request(method, request_path, body=payload, headers=headers)
            response = conn.getresponse()
            response.fp.raw._sock.settimeout(READ_TIMEOUT)  # type: ignore[union-attr]
            return response, conn
        except (socket.timeout, TimeoutError) as exc:
            conn.close()
            raise HermesTimeoutError(operation=operation) from exc
        except (OSError, ConnectionError) as exc:
            conn.close()
            raise HermesUnavailableError(operation=operation) from exc

    def _raise_http(self, status: int, operation: str, *, resource: str | None = None) -> None:
        if status in {401, 403}:
            raise HermesAuthError(status=status, operation=operation)
        if status == 404 and resource:
            raise HermesNotFoundError(resource, operation=operation)
        raise HermesApiError("HERMES_UPSTREAM_HTTP_ERROR", f"Hermes returned HTTP {status}", status=status, operation=operation)


def parse_sse_events(lines: Iterable[str] | Any) -> Iterator[dict[str, Any]]:
    """Parse an SSE response, preserving upstream event/id/data information."""
    event: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []

    def emit() -> dict[str, Any] | None:
        nonlocal event, event_id, data_lines
        if not data_lines:
            event = event_id = None
            return None
        raw = "\n".join(data_lines)
        result: dict[str, Any] = {"event": event or "message", "id": event_id, "data": raw}
        try:
            result["json"] = json.loads(raw)
        except json.JSONDecodeError:
            result["upstream_event"] = result["event"]
            result["event"] = "protocol.error"
            result["error"] = {"code": "HERMES_UPSTREAM_PROTOCOL_ERROR", "message": "invalid SSE JSON"}
        event = event_id = None
        data_lines = []
        return result

    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.rstrip("\r\n")
        if line == "":
            item = emit()
            if item is not None:
                yield item
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value)
    item = emit()
    if item is not None:
        yield item


__all__ = ["HermesApiClient", "HermesApiError", "HermesAuthError", "HermesNotFoundError", "HermesUnavailableError", "HermesTimeoutError", "HermesProtocolError", "parse_sse_events"]
