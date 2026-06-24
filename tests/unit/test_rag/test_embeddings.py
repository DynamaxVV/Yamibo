from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yamibo_mcp.rag.embeddings import build_embedding_provider
from yamibo_mcp.services.llm_client import LLMRequestError


class _FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class _FakeOpener:
    def __init__(self, captured: dict[str, object]):
        self._captured = captured

    def open(self, request, timeout=60):
        self._captured["url"] = request.full_url
        self._captured["body"] = json.loads(request.data.decode("utf-8"))
        self._captured["headers"] = dict(request.header_items())
        self._captured["timeout"] = timeout
        return _FakeResponse({"data": [{"embedding": [0.1, 0.2, 0.3]}]})


class _SequencedOpener:
    def __init__(self, captured: dict[str, object], responses: list[dict[str, object]]):
        self._captured = captured
        self._responses = iter(responses)

    def open(self, request, timeout=60):
        self._captured.setdefault("bodies", []).append(json.loads(request.data.decode("utf-8")))
        self._captured.setdefault("urls", []).append(request.full_url)
        self._captured.setdefault("timeouts", []).append(timeout)
        return _FakeResponse(next(self._responses))


def _settings():
    return SimpleNamespace(
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_base_url="https://api.302.ai/v1",
        rag_api_key="token",
        rag_debug_indexing=False,
    )


def test_embedding_provider_sends_string_input_and_dimensions_for_text_embedding_3(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr("urllib.request.build_opener", lambda *args, **kwargs: _FakeOpener(captured))

    provider = build_embedding_provider(_settings())
    result = provider.embed_texts(["hello"])

    assert result == [[0.1, 0.2, 0.3]]
    assert captured["url"] == "https://api.302.ai/v1/embeddings"
    assert captured["body"]["input"] == "hello"
    assert captured["body"]["dimensions"] == 512
    assert captured["body"]["model"] == "text-embedding-3-small"
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_embedding_provider_omits_dimensions_for_ada_model(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr("urllib.request.build_opener", lambda *args, **kwargs: _FakeOpener(captured))

    settings = SimpleNamespace(
        rag_embedding_model="text-embedding-ada-002",
        rag_embedding_dimensions=1536,
        rag_base_url="https://api.302.ai/v1",
        rag_api_key="token",
        rag_debug_indexing=False,
    )
    provider = build_embedding_provider(settings)
    result = provider.embed_texts(["hello"])

    assert result == [[0.1, 0.2, 0.3]]
    assert captured["body"]["input"] == "hello"
    assert "dimensions" not in captured["body"]
    assert captured["body"]["model"] == "text-embedding-ada-002"


def test_embedding_provider_debug_logs_missing_data_response(monkeypatch, caplog):
    captured: dict[str, object] = {}

    class _MissingDataResponse(_FakeResponse):
        pass

    class _MissingDataOpener(_FakeOpener):
        def open(self, request, timeout=60):
            self._captured["url"] = request.full_url
            self._captured["body"] = json.loads(request.data.decode("utf-8"))
            self._captured["headers"] = dict(request.header_items())
            self._captured["timeout"] = timeout
            return _MissingDataResponse({"error": {"message": "no data"}})

    monkeypatch.setattr("urllib.request.build_opener", lambda *args, **kwargs: _MissingDataOpener(captured))

    settings = _settings()
    settings = SimpleNamespace(**{**settings.__dict__, "rag_debug_indexing": True})

    provider = build_embedding_provider(settings)
    with caplog.at_level("WARNING"):
        with pytest.raises(LLMRequestError) as exc_info:
            provider.embed_texts(["hello"])

    assert "missing data field" in str(exc_info.value)
    assert "[RAG-DEBUG] embedding response missing data field" in caplog.text
    assert '"error"' in caplog.text


def test_embedding_provider_batches_large_input_and_preserves_order(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr("yamibo_mcp.rag.embeddings._EMBEDDING_BATCH_SIZE", 2)
    opener = _SequencedOpener(
        captured,
        [
            {"data": [{"embedding": [0.1]}, {"embedding": [0.2]}]},
            {"data": [{"embedding": [0.3]}]},
        ],
    )
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *args, **kwargs: opener,
    )

    provider = build_embedding_provider(_settings())
    result = provider.embed_texts(["a", "b", "c"])

    assert result == [[0.1], [0.2], [0.3]]
    assert captured["bodies"][0]["input"] == ["a", "b"]
    assert captured["bodies"][1]["input"] == "c"
