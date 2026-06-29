from __future__ import annotations

import json
import urllib.request
from unittest.mock import MagicMock

import pytest

from yamibo_mcp.config import Settings
from yamibo_mcp.yamibo.proxy_pool import (
    MihomoControllerClient,
    MihomoProxyPoolConfig,
    ProxyBinding,
    _filter_nodes,
    check_proxy_pool_health,
    clear_proxy_cache,
    select_thread_proxy,
)


# --- fake mihomo controller responses ---

_PROXIES_RESPONSE = {
    "proxies": {
        "yamibo": {
            "type": "Selector",
            "now": "node-a",
            "all": ["node-a", "node-b", "node-c"],
        },
        "GLOBAL": {
            "type": "Selector",
            "now": "yamibo",
            "all": ["yamibo", "DIRECT"],
        },
    }
}


def _make_fake_opener(responses: list[dict]):
    """Return an opener that serves canned JSON responses in order."""
    call_count = {"count": 0}

    class _FakeResponse:
        def __init__(self, data, status=200):
            self._data = json.dumps(data).encode("utf-8")
            self.status = status
            self._url = "http://127.0.0.1:9090"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return self._url

        def read(self):
            return self._data

    def _open(req, timeout=None):
        idx = call_count["count"]
        call_count["count"] += 1
        if idx < len(responses):
            return _FakeResponse(responses[idx])
        return _FakeResponse({"error": "no more canned responses"}, status=500)

    opener = MagicMock()
    opener.open.side_effect = _open
    return opener


# --- ProxyBinding ---


def test_proxy_binding_fields():
    b = ProxyBinding(
        group="g1",
        node="n1",
        proxy_url="http://127.0.0.1:7890",
        best_effort=True,
        diagnostics={"delay_ms": 42},
    )
    assert b.group == "g1"
    assert b.node == "n1"
    assert b.proxy_url == "http://127.0.0.1:7890"
    assert b.best_effort is True
    assert b.diagnostics == {"delay_ms": 42}


# --- MihomoControllerClient ---


def test_client_discovers_nodes():
    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="test-secret",
        selector_group="yamibo",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )
    client._opener = _make_fake_opener(
        [_PROXIES_RESPONSE, {"delay": 100}, {"delay": 200}, {"delay": 150}]
    )

    nodes = client.discover_nodes()
    assert nodes == ["node-a", "node-b", "node-c"]


def test_client_discover_no_group():
    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="",
        selector_group="nonexistent",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )
    client._opener = _make_fake_opener([{"proxies": {}}])
    nodes = client.discover_nodes()
    assert nodes == []


def test_client_test_node_returns_delay():
    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="",
        selector_group="yamibo",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )
    client._opener = _make_fake_opener([{"delay": 234}])
    delay = client.test_node_delay("node-b")
    assert delay == 234


def test_client_test_node_timeout_returns_none():
    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="",
        selector_group="yamibo",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )
    client._opener = _make_fake_opener([])  # no canned responses → error
    delay = client.test_node_delay("node-b")
    assert delay is None


def test_client_select_node():
    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="",
        selector_group="yamibo",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )
    # No content response for PUT
    client._opener = _make_fake_opener([{}])
    # Should not raise
    client.select_node("node-c")


def test_client_authorization_header(monkeypatch):
    """Secret → Bearer auth; no secret → no auth header."""
    captured_requests: list[urllib.request.Request] = []
    opener = MagicMock()

    def _capture(req, timeout=None):
        captured_requests.append(req)
        return _make_fake_opener([{"delay": 100}]).open(req)

    opener.open.side_effect = _capture

    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="my-secret",
        selector_group="g",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )
    client._opener = opener
    client.test_node_delay("n")
    assert captured_requests[0].get_header("Authorization") == "Bearer my-secret"

    captured_requests.clear()
    client2 = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="",
        selector_group="g",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )
    client2._opener = opener
    client2.test_node_delay("n")
    assert captured_requests[0].get_header("Authorization") is None


# --- select_thread_proxy ---


def _make_settings(enabled: bool = True) -> Settings:
    return Settings(
        project_root=__import__("pathlib").Path("/fake"),
        config_path=__import__("pathlib").Path("/fake/yamibo.local.json"),
        data_dir=__import__("pathlib").Path("/fake/data"),
        db_path=__import__("pathlib").Path("/fake/data/forum.db"),
        db_backend="sqlite",
        db_url=None,
        db_pool_min=1,
        db_pool_max=5,
        db_pool_timeout=30.0,
        db_connect_timeout=10.0,
        db_schema="public",
        db_ssl_mode="prefer",
        db_ssl_root_cert=None,
        title_hints_path=__import__("pathlib").Path("/fake/data/title_hints.json"),
        web_host="0.0.0.0",
        web_port=8765,
        worker_id=None,
        jobs_enabled=True,
        worker_poll_seconds=2.0,
        worker_parallelism=2,
        worker_lease_seconds=60,
        worker_heartbeat_seconds=15,
        cookie_file=__import__("pathlib").Path("/fake/.cookie"),
        login_username=None,
        login_password=None,
        use_system_proxy=False,
        image_download_timeout_seconds=45.0,
        image_download_retries=2,
        archive_thread_max_pages=50,
        novel_author_only_max_pages=50,
        novel_author_only_page_delay_seconds=0.5,
        export_dir=__import__("pathlib").Path("/fake/data/exports"),
        novel_txt_export_dir=__import__("pathlib").Path("/fake/data/novel_exports"),
        export_default_strategy="cache_only",
        export_stale_after_hours=24,
        novel_txt_include_filtered_notes=False,
        novel_txt_debug_markers=False,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key=None,
        llm_model="gpt-4.1-mini",
        rag_enabled=True,
        rag_base_url="https://api.openai.com/v1",
        rag_api_key=None,
        rag_debug_indexing=False,
        rag_embedding_provider="openai",
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_chunker_version="rag-chunker-v1",
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=900,
        rag_hybrid_fts_candidates=50,
        rag_hybrid_vector_candidates=50,
        title_parse_use_llm=True,
        common_scanlation_groups=[],
        common_authors=[],
        backup_dir=__import__("pathlib").Path("/fake/data/backups"),
        backup_keep_count=20,
        cleanup_staging_older_than_hours=48,
        request_timeout_seconds=30.0,
        request_interval_seconds=1.0,
        request_interval_jitter_seconds=0.5,
        account_pool=(),
        proxy_pool=MihomoProxyPoolConfig(
            enabled=enabled,
            controller_url="http://127.0.0.1:9090",
            secret="test-secret",
            proxy_url="http://127.0.0.1:7890",
            selector_group="yamibo",
            test_url="https://www.gstatic.com/generate_204",
            test_timeout_ms=3000,
            failure_policy="fail_open",
            allowed_patterns=(),
            denied_patterns=(),
            max_delay_ms=0,
        ),
        cookie_refresh_interval_hours=12.0,
    )


def test_select_thread_proxy_disabled_returns_none():
    settings = _make_settings(enabled=False)
    result = select_thread_proxy(settings, tid=12345)
    assert result is None


def test_select_thread_proxy_stable_selection():
    """Same tid always picks the same node."""
    settings = _make_settings(enabled=True)

    calls = {"count": 0}

    def _fake_discover(self):
        return ["node-a", "node-b", "node-c"]

    def _fake_test(self, node):
        return 100 + hash(node) % 200

    def _fake_select(self, node):
        calls["count"] += 1

    from yamibo_mcp.yamibo import proxy_pool as mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod.MihomoControllerClient, "discover_nodes", _fake_discover)
    monkeypatch.setattr(mod.MihomoControllerClient, "test_node_delay", _fake_test)
    monkeypatch.setattr(mod.MihomoControllerClient, "select_node", _fake_select)

    try:
        a = select_thread_proxy(settings, tid=42)
        b = select_thread_proxy(settings, tid=42)
        assert a is not None
        assert b is not None
        assert a.node == b.node
    finally:
        monkeypatch.undo()


def test_select_thread_proxy_fallback_on_no_nodes():
    clear_proxy_cache()
    settings = _make_settings(enabled=True)

    from yamibo_mcp.yamibo import proxy_pool as mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod.MihomoControllerClient, "discover_nodes", lambda self: [])
    try:
        result = select_thread_proxy(settings, tid=12345)
        assert result is None
    finally:
        monkeypatch.undo()


# --- check_proxy_pool_health ---


def test_check_proxy_pool_health_disabled_reports_error():
    settings = _make_settings(enabled=False)
    result = check_proxy_pool_health(settings)
    assert result["ok"] is False
    assert result["error"] == "proxy_pool is disabled"


def test_check_proxy_pool_health_not_configured():
    from types import SimpleNamespace

    settings = SimpleNamespace()
    result = check_proxy_pool_health(settings)
    assert result["ok"] is False
    assert "not configured" in result["error"]


def test_check_proxy_pool_health_controller_unreachable(monkeypatch):
    settings = _make_settings(enabled=True)

    def _fail_request(self, path, method="GET", body=None):
        raise OSError("connection refused")

    monkeypatch.setattr(
        "yamibo_mcp.yamibo.proxy_pool.MihomoControllerClient._request",
        _fail_request,
    )
    result = check_proxy_pool_health(settings)
    assert result["ok"] is False
    assert "controller unreachable" in result["error"]
    assert result["controller"]["reachable"] is False


def test_check_proxy_pool_health_missing_selector_group(monkeypatch):
    settings = _make_settings(enabled=True)

    def _fake_request(self, path, method="GET", body=None):
        return {"proxies": {"GLOBAL": {"type": "Selector", "now": "DIRECT", "all": ["DIRECT"]}}}

    monkeypatch.setattr(
        "yamibo_mcp.yamibo.proxy_pool.MihomoControllerClient._request",
        _fake_request,
    )
    result = check_proxy_pool_health(settings)
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert "yamibo" in result["error"]


def test_check_proxy_pool_health_all_nodes_tested(monkeypatch):
    settings = _make_settings(enabled=True)

    def _fake_request(self, path, method="GET", body=None):
        if path == "/proxies":
            return {
                "proxies": {
                    "yamibo": {
                        "type": "Selector",
                        "now": "node-a",
                        "all": ["node-a", "node-b", "node-c"],
                    }
                }
            }
        if "/delay" in path:
            return {"delay": 150}
        return {}

    monkeypatch.setattr(
        "yamibo_mcp.yamibo.proxy_pool.MihomoControllerClient._request",
        _fake_request,
    )
    result = check_proxy_pool_health(settings)
    assert result["ok"] is True
    assert result["controller"]["reachable"] is True
    assert result["selector_group"]["exists"] is True
    assert result["selector_group"]["current_node"] == "node-a"
    assert result["selector_group"]["node_count"] == 3
    assert result["usable_count"] == 3
    assert result["min_delay_ms"] == 150
    assert result["max_delay_ms"] == 150
    assert [n["name"] for n in result["nodes"]] == ["node-a", "node-b", "node-c"]


def test_select_thread_proxy_uses_cache_on_second_call(monkeypatch):
    """Second call should hit cache and skip discover + delay tests."""
    clear_proxy_cache()
    settings = _make_settings(enabled=True)
    call_counts = {"discover": 0, "delay": 0}

    def _fake_discover(self):
        call_counts["discover"] += 1
        return ["node-a", "node-b"]

    def _fake_test_all(self, nodes):
        call_counts["delay"] += 1
        return [("node-a", 100), ("node-b", 200)]

    def _fake_select(self, node):
        pass

    monkeypatch.setattr(
        "yamibo_mcp.yamibo.proxy_pool.MihomoControllerClient.discover_nodes",
        _fake_discover,
    )
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.proxy_pool.MihomoControllerClient.test_all_nodes_parallel",
        _fake_test_all,
    )
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.proxy_pool.MihomoControllerClient.select_node",
        _fake_select,
    )

    r1 = select_thread_proxy(settings, tid=42)
    assert r1 is not None
    assert call_counts == {"discover": 1, "delay": 1}

    # Second call — cache hit, no additional discover or delay calls
    r2 = select_thread_proxy(settings, tid=42)
    assert r2 is not None
    assert r2.node == r1.node
    assert r2.diagnostics["from_cache"] is True
    assert call_counts == {"discover": 1, "delay": 1}


def test_select_thread_proxy_cache_expires(monkeypatch):
    """Cache should expire after TTL and trigger fresh test."""
    from yamibo_mcp.yamibo import proxy_pool as mod

    clear_proxy_cache()
    settings = _make_settings(enabled=True)
    call_counts = {"discover": 0, "delay": 0}

    def _fake_discover(self):
        call_counts["discover"] += 1
        return ["node-a", "node-b"]

    def _fake_test_all(self, nodes):
        call_counts["delay"] += 1
        return [("node-a", 100), ("node-b", 200)]

    def _fake_select(self, node):
        pass

    monkeypatch.setattr(
        mod.MihomoControllerClient, "discover_nodes", _fake_discover,
    )
    monkeypatch.setattr(
        mod.MihomoControllerClient, "test_all_nodes_parallel", _fake_test_all,
    )
    monkeypatch.setattr(
        mod.MihomoControllerClient, "select_node", _fake_select,
    )

    # First call
    select_thread_proxy(settings, tid=42)
    assert call_counts == {"discover": 1, "delay": 1}

    # Force expire cache
    monkeypatch.setattr(mod, "_CACHE_TTL_SECONDS", -1.0)

    # Second call — cache expired, re-fetches
    select_thread_proxy(settings, tid=42)
    assert call_counts == {"discover": 2, "delay": 2}


def test_clear_proxy_cache_removes_all_entries():
    clear_proxy_cache()
    settings = _make_settings(enabled=True)

    from yamibo_mcp.yamibo import proxy_pool as mod

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(mod.MihomoControllerClient, "discover_nodes", lambda self: ["a", "b"])
    monkeypatch.setattr(mod.MihomoControllerClient, "test_all_nodes_parallel", lambda self, nodes: [(n, 100) for n in nodes])
    monkeypatch.setattr(mod.MihomoControllerClient, "select_node", lambda self, node: None)

    try:
        select_thread_proxy(settings, tid=1)
        assert clear_proxy_cache() == 1
        assert clear_proxy_cache() == 0  # already empty
    finally:
        monkeypatch.undo()


def test_test_all_nodes_parallel_returns_sorted_by_delay():
    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="",
        selector_group="yamibo",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )

    delay_map = {"a": 300, "b": 100, "c": 200}
    original_test = client.test_node_delay

    def _fake_delay(node):
        return delay_map.get(node)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(client, "test_node_delay", _fake_delay)
    try:
        results = client.test_all_nodes_parallel(["a", "b", "c"])
        # Should be sorted by delay ascending
        assert [n for n, d in results] == ["b", "c", "a"]
        assert [d for n, d in results] == [100, 200, 300]
    finally:
        monkeypatch.undo()


def test_test_all_nodes_parallel_filters_timeouts():
    client = MihomoControllerClient(
        controller_url="http://127.0.0.1:9090",
        secret="",
        selector_group="yamibo",
        test_url="https://www.gstatic.com/generate_204",
        test_timeout_ms=3000,
    )

    def _fake_delay(node):
        if node == "dead":
            return None
        return 100

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(client, "test_node_delay", _fake_delay)
    try:
        results = client.test_all_nodes_parallel(["alive", "dead"])
        assert results == [("alive", 100)]
    finally:
        monkeypatch.undo()


# --- _filter_nodes ---


def test_filter_nodes_no_rules_passes_all():
    nodes = [("🇭🇰 香港 01", 100), ("🇸🇬 新加坡 02", 200)]
    result = _filter_nodes(nodes)
    assert result == nodes


def test_filter_nodes_denied_pattern_excludes_match():
    nodes = [("🇭🇰 香港 01", 100), ("🇸🇬 新加坡 02", 200), ("🇯🇵 日本 03", 150)]
    result = _filter_nodes(nodes, denied_patterns=("新加坡",))
    assert result == [("🇭🇰 香港 01", 100), ("🇯🇵 日本 03", 150)]


def test_filter_nodes_allowed_pattern_only_keeps_match():
    nodes = [("🇭🇰 香港 01", 100), ("🇸🇬 新加坡 02", 200), ("🇯🇵 日本 03", 150)]
    result = _filter_nodes(nodes, allowed_patterns=("香港|日本",))
    assert result == [("🇭🇰 香港 01", 100), ("🇯🇵 日本 03", 150)]


def test_filter_nodes_max_delay_excludes_slow():
    nodes = [("node-a", 50), ("node-b", 200), ("node-c", 350)]
    result = _filter_nodes(nodes, max_delay_ms=300)
    assert result == [("node-a", 50), ("node-b", 200)]


def test_filter_nodes_combined_rules():
    nodes = [
        ("🇭🇰 HK-01", 80),
        ("🇭🇰 HK-02", 400),   # too slow
        ("🇸🇬 SG-01", 120),    # denied
        ("🇯🇵 JP-01", 60),
        ("🇯🇵 JP-02", 250),
    ]
    result = _filter_nodes(
        nodes,
        allowed_patterns=("HK|JP",),     # only HK or JP
        denied_patterns=("HK-02",),       # exclude this specific one
        max_delay_ms=300,                 # HK-02 (400) and JP-02 (250) excluded
    )
    # HK-01: allowed ✓, not denied ✓, delay 80 ✓ → kept
    # HK-02: allowed ✓, denied ✗ → removed by denied
    # SG-01: not allowed → removed by allowed
    # JP-01: allowed ✓, not denied ✓, delay 60 ✓ → kept
    # JP-02: allowed ✓, not denied ✓, delay 250 ✓ → kept
    expected = [("🇭🇰 HK-01", 80), ("🇯🇵 JP-01", 60), ("🇯🇵 JP-02", 250)]
    assert result == expected


def test_filter_nodes_empty_after_filter_returns_empty():
    nodes = [("node-a", 100), ("node-b", 200)]
    result = _filter_nodes(nodes, max_delay_ms=50)
    assert result == []
