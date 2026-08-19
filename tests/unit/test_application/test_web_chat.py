from unittest.mock import Mock

import pytest

from yamibo_mcp.services.web_chat import ChatService, ChatServiceError, REQUIRED_CAPABILITIES
from yamibo_mcp.services.hermes_api import HermesApiError, HermesTimeoutError


class Client:
    endpoint = "http://hermes:8642"; api_key = "secret"; model = "hermes-agent"
    def __init__(self): self.calls = []
    def health(self): self.calls.append("health"); return {"version": "1"}
    def capabilities(self): self.calls.append("capabilities"); return {"capabilities": sorted(REQUIRED_CAPABILITIES)}
    def models(self): self.calls.append("models"); return {"models": []}


def test_probe_order_cache_and_force():
    c = Client(); now = [0.0]; s = ChatService(client=c, clock=lambda: now[0])
    assert s.probe_capabilities()["ready"] and c.calls == ["health", "capabilities", "models"]
    s.probe_capabilities(); assert c.calls == ["health", "capabilities", "models"]
    now[0] = 31; s.probe_capabilities(force=True); assert c.calls[-3:] == ["health", "capabilities", "models"]


def test_context_never_returns_key_and_missing_is_not_ready():
    c = Client(); c.capabilities = lambda: {"capabilities": []}
    result = ChatService(client=c).context()
    assert result["ready"] is False and result["error"]["code"] == "HERMES_CAPABILITY_MISSING"
    assert "secret" not in str(result) and result["hermes"]["has_api_key"] is True
    assert result["streaming_enabled"] is True


def test_history_is_complete_deterministic_and_input_not_added():
    c = Client(); c.get_session = Mock(return_value={"object": "hermes.session", "session": {"id": "s"}})
    c.get_messages = Mock(return_value={"object": "list", "session_id": "s", "data": [{"role": "system", "content": {"z": 1, "a": "中"}}, {"role": "tool", "content": ""}]})
    registry = Mock(); registry.submit.return_value = Mock()
    ChatService(client=c, registry=registry).start_run("s", "input")
    history = registry.submit.call_args.kwargs["history"]
    assert history == [{"role": "system", "content": '{"a":"中","z":1}'}, {"role": "tool", "content": ""}]
    assert "input" not in [x["content"] for x in history]


def test_history_failure_never_submits():
    c = Client(); c.get_session = Mock(return_value={"object": "hermes.session", "session": {"id": "s"}}); c.get_messages = Mock(side_effect=RuntimeError())
    registry = Mock()
    with pytest.raises(ChatServiceError) as err: ChatService(client=c, registry=registry).start_run("s", "x")
    assert err.value.code == "CHAT_HISTORY_UNAVAILABLE"; registry.submit.assert_not_called()


def test_timeout_maps_to_unavailable():
    c = Client(); c.health = Mock(side_effect=HermesTimeoutError())
    with pytest.raises(ChatServiceError) as err: ChatService(client=c).probe_capabilities()
    assert (err.value.code, err.value.http_status, err.value.retryable) == ("HERMES_UNAVAILABLE", 503, True)


class CompatClient:
    endpoint = "http://hermes:18642"; api_key = "secret"; model = "hermes-agent"

    def health(self): return {"version": "0.10.0"}
    def capabilities(self): raise HermesApiError("HERMES_UPSTREAM_HTTP_ERROR", "not found", status=404, operation="capabilities")
    def models(self): return {"data": [{"id": "hermes-agent"}]}
    def chat_completion(self, *, messages, stream=False): return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
    def iter_chat_completion_events(self, *, messages):
        yield {"choices": [{"delta": {"role": "assistant", "content": "ok"}}]}
        yield {"done": True}


class TimeoutCompatClient(CompatClient):
    def chat_completion(self, *, messages, stream=False):
        raise HermesTimeoutError(operation="chat_completion")


def test_missing_capability_endpoint_selects_chat_completions_fallback(tmp_path):
    from types import SimpleNamespace

    service = ChatService(settings=SimpleNamespace(data_dir=tmp_path), client=CompatClient())
    context = service.context()
    assert context["ready"] is True
    assert context["mode"] == "hermes_http"
    assert context["transport"] == "hermes_http"
    assert context["degraded"] is True
    assert context["hermes"]["capabilities_source"] == "inferred"
    assert "chat_completions" in context["hermes"]["capabilities"]


def test_compatibility_probe_timeout_returns_diagnostic_context(tmp_path):
    from types import SimpleNamespace

    service = ChatService(settings=SimpleNamespace(data_dir=tmp_path), client=TimeoutCompatClient())
    context = service.context()

    assert context["ready"] is False
    assert context["transport"] == "unavailable"
    assert context["error"]["code"] == "HERMES_UNAVAILABLE"


def test_chat_completions_fallback_reuses_local_sessions_and_run_events(tmp_path):
    from types import SimpleNamespace

    service = ChatService(settings=SimpleNamespace(data_dir=tmp_path), client=CompatClient())
    session = service.create_session(title="compat")
    run = service.start_run(session["id"], "hello")
    service.registry.join(run.run_id, timeout=2)
    subscription = service.subscribe(run.run_id)
    events = [subscription.get(timeout=1), subscription.get(timeout=1), subscription.get(timeout=1)]
    subscription.close()
    assert [event.type for event in events] == ["message.delta", "run.completed", "session.reconciled"]
    messages = service.get_messages(session["id"])["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == "ok"


def test_session_envelopes_are_normalized_to_local_contract():
    c = Client()
    c.list_sessions = Mock(return_value={"object": "list", "data": [{"id": "s1"}], "has_more": True, "limit": 50, "offset": 0})
    c.create_session = Mock(return_value={"object": "hermes.session", "session": {"id": "s1", "title": "新对话"}})
    c.get_session = Mock(return_value={"object": "hermes.session", "session": {"id": "s1"}})
    c.get_messages = Mock(return_value={"object": "list", "session_id": "s1", "data": [{"role": "user", "content": "hi"}]})
    service = ChatService(client=c)
    assert service.list_sessions() == {"sessions": [{"id": "s1"}], "items": [{"id": "s1"}], "has_more": True, "limit": 50, "offset": 0}
    assert service.create_session()["id"] == "s1"
    assert service.get_session("s1")["id"] == "s1"
    assert service.get_messages("s1")["messages"][0]["content"] == "hi"
    assert service.get_messages("s1")["list"] == [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize("payload", [{"object": "list", "data": [], "has_more": False}, {"object": "list", "data": [], "has_more": False, "limit": 1, "offset": "0"}])
def test_invalid_session_list_envelope_maps_to_protocol_error(payload):
    c = Client(); c.list_sessions = Mock(return_value=payload)
    with pytest.raises(ChatServiceError) as err: ChatService(client=c).list_sessions()
    assert (err.value.code, err.value.http_status) == ("CHAT_UPSTREAM_PROTOCOL_ERROR", 502)


def test_invalid_messages_envelope_maps_to_protocol_error():
    c = Client(); c.get_messages = Mock(return_value={"object": "list", "session_id": "s", "data": {}})
    with pytest.raises(ChatServiceError) as err: ChatService(client=c).get_messages("s")
    assert (err.value.code, err.value.http_status) == ("CHAT_UPSTREAM_PROTOCOL_ERROR", 502)
