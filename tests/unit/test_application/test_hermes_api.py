from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from yamibo_mcp.services.hermes_api import (
    HermesApiClient,
    HermesApiError,
    HermesNotFoundError,
    HermesTimeoutError,
    parse_sse_events,
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[tuple[str, str, dict[str, str], bytes]] = []
    response_status = 200
    response_body: bytes = b'{}'
    response_type = "application/json"
    finished = 0
    response_delay = 0.0

    def log_message(self, *_args):
        pass

    def finish(self):
        type(self).finished += 1
        super().finish()

    def _reply(self):
        length = len(type(self).response_body)
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", type(self).response_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()
        if type(self).response_delay:
            time.sleep(type(self).response_delay)
        if length:
            self.wfile.write(type(self).response_body)

    def _record(self):
        length = int(self.headers.get("Content-Length", "0"))
        type(self).requests.append((self.command, self.path, dict(self.headers), self.rfile.read(length)))
        self._reply()

    do_GET = _record
    do_POST = _record
    do_PATCH = _record
    do_DELETE = _record


@pytest.fixture
def server():
    _Handler.requests = []
    _Handler.finished = 0
    _Handler.response_status = 200
    _Handler.response_body = b'{}'
    _Handler.response_type = "application/json"
    _Handler.response_delay = 0.0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def client(server):
    return HermesApiClient(endpoint=f"http://127.0.0.1:{server.server_port}", api_key="test-key", model="test-model")


def test_contract_methods_paths_query_auth_and_bodies(server):
    c = client(server)
    assert c.health() == {}
    c.list_sessions(limit=50, offset=2, source="web chat", include_children=True)
    c.create_session(title="new", model="test-model")
    c.get_session("id/with space")
    c.update_session("id/with space", {"title": "renamed"})
    c.delete_session("id/with space")
    c.get_messages("id/with space")
    c.start_run(session_id="id/with space", input_text="hello", history=[])
    c.get_run("run/1")
    c.stop_run("run/1")
    c.approve_run("run/1", choice="once", resolve_all=False)
    paths = [item[1] for item in _Handler.requests]
    assert paths[1] == "/api/sessions?limit=50&offset=2&source=web+chat&include_children=true"
    assert "/api/sessions/id%2Fwith%20space" in paths
    assert "/v1/runs/run%2F1" in paths
    for method, _path, headers, body in _Handler.requests:
        assert headers["Authorization"] == "Bearer test-key"
        if method in {"POST", "PATCH"}:
            assert headers["Content-Type"] == "application/json"
    run_body = json.loads(next(body for method, path, _, body in _Handler.requests if method == "POST" and path == "/v1/runs"))
    assert run_body == {"model": "test-model", "session_id": "id/with space", "input": "hello", "conversation_history": []}


def test_chat_completion_contract(server):
    c = client(server)
    assert c.chat_completion(messages=[{"role": "user", "content": "hello"}], stream=False) == {}
    request = _Handler.requests[0]
    assert request[0:2] == ("POST", "/v1/chat/completions")
    assert json.loads(request[3]) == {"model": "test-model", "messages": [{"role": "user", "content": "hello"}], "stream": False}


def test_chat_completion_sse_decodes_openai_chunks_and_done(server):
    _Handler.response_type = "text/event-stream"
    _Handler.response_body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    events = list(client(server).iter_chat_completion_events(messages=[{"role": "user", "content": "hello"}]))
    assert events == [{"choices": [{"delta": {"content": "hi"}}]}, {"done": True}]
    assert _Handler.requests[0][1] == "/v1/chat/completions"


def test_session_and_approval_bodies_are_sent_exactly(server):
    c = client(server)
    c.create_session(title="new", model="test-model")
    c.update_session("s", {"title": "renamed", "end_reason": "done"})
    c.approve_run("r", choice="always", resolve_all=True)

    requests = _Handler.requests
    assert requests[0][0:2] == ("POST", "/api/sessions")
    assert json.loads(requests[0][3]) == {"title": "new", "model": "test-model"}
    assert requests[1][0:2] == ("PATCH", "/api/sessions/s")
    assert json.loads(requests[1][3]) == {"title": "renamed", "end_reason": "done"}
    assert requests[2][0:2] == ("POST", "/v1/runs/r/approval")
    assert json.loads(requests[2][3]) == {"choice": "always", "resolve_all": True}


def test_statuses_and_safe_error_mapping(server):
    c = client(server)
    _Handler.response_status = 201
    _Handler.response_body = b'{"id":"s"}'
    assert c.create_session(title="x", model="m") == {"id": "s"}
    _Handler.response_status = 202
    _Handler.response_body = b'{"run_id":"r"}'
    assert c.start_run(session_id="s", input_text="x", history=[])["run_id"] == "r"
    _Handler.response_status = 204
    _Handler.response_body = b""
    assert c.stop_run("r") == {}
    _Handler.response_status = 404
    with pytest.raises(HermesNotFoundError) as exc:
        c.get_session("s")
    assert exc.value.code == "HERMES_SESSION_NOT_FOUND"
    _Handler.response_status = 401
    with pytest.raises(HermesApiError) as exc:
        c.health()
    assert exc.value.code == "HERMES_AUTH_FAILED"
    _Handler.response_status = 500
    with pytest.raises(HermesApiError) as exc:
        c.health()
    assert exc.value.code == "HERMES_UPSTREAM_HTTP_ERROR"
    assert "test-key" not in str(exc.value)


def test_sse_boundaries_and_invalid_json():
    raw = [
        ": keepalive\r\n",
        "id: 7\r\n",
        "event: message.delta\r\n",
        'data: {"a":\r\n',
        'data: 1}\r\n',
        '\r\n',
        "event: plain\n",
        "data: first\n",
        "data: second\n",
        "\n",
        "event: broken\n",
        "id: 9\n",
        "data: nope",
    ]
    events = list(parse_sse_events(raw))
    assert events[0] == {"event": "message.delta", "id": "7", "data": '{"a":\n1}', "json": {"a": 1}}
    assert events[1]["data"] == "first\nsecond"
    assert events[2]["event"] == "protocol.error"
    assert events[2]["error"]["code"] == "HERMES_UPSTREAM_PROTOCOL_ERROR"


def test_sse_fake_endpoint(server):
    _Handler.response_status = 200
    _Handler.response_type = "text/event-stream"
    _Handler.response_body = b'id: 1\nevent: done\ndata: {"ok":true}\n\n'
    assert list(client(server).iter_run_events("run id"))[0]["json"] == {"ok": True}
    assert _Handler.requests[0][1] == "/v1/runs/run%20id/events"


def test_invalid_sse_json_closes_upstream_connection(server):
    _Handler.response_status = 200
    _Handler.response_type = "text/event-stream"
    _Handler.response_body = b"event: broken\ndata: nope\n\n"

    events = list(client(server).iter_run_events("run"))

    assert events[0]["event"] == "protocol.error"
    assert _Handler.finished == 1


def test_connection_failure_and_timeout_mapping():
    with pytest.raises((HermesApiError, HermesTimeoutError)) as exc:
        HermesApiClient(endpoint="http://127.0.0.1:1", api_key="test-key").health()
    assert "test-key" not in str(exc.value)


def test_read_timeout_is_mapped_without_leaking_key(server, monkeypatch):
    _Handler.response_body = b'{"late":true}'
    _Handler.response_delay = 0.05
    monkeypatch.setattr("yamibo_mcp.services.hermes_api.READ_TIMEOUT", 0.01)

    with pytest.raises(HermesTimeoutError) as exc:
        client(server).health()

    assert exc.value.code == "HERMES_TIMEOUT"
    assert "test-key" not in str(exc.value)
