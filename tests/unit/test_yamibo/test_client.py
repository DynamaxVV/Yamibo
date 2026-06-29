from __future__ import annotations

import http.client
import urllib.request
from http.cookiejar import Cookie
from unittest.mock import MagicMock

import pytest

from yamibo_mcp.errors import RemoteFetchError, ThreadPermissionRequiredError, UnexpectedPageError
from yamibo_mcp.yamibo.client import FetchResult, YamiboClient
from yamibo_mcp.yamibo import runtime_limits


class _TimeoutResponse:
    def __init__(self, *, url: str = "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1") -> None:
        self._url = url
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self) -> str:
        return self._url

    def read(self):
        raise TimeoutError("The read operation timed out")


class _FakeOpener:
    def open(self, request, timeout=None):
        return _TimeoutResponse()


def test_fetch_url_wraps_read_timeout_as_remote_fetch_error():
    client = YamiboClient(timeout=0.1, retries=0)
    client.opener = _FakeOpener()

    with pytest.raises(RemoteFetchError, match="failed to read"):
        client.fetch_url("https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1")


def test_login_read_timeout_wraps_as_remote_fetch_error():
    client = YamiboClient(timeout=0.1, retries=0, login_username="u", login_password="p")

    login_page = MagicMock()
    login_page.html = (
        '<form action="member.php?mod=logging&action=login&loginsubmit=yes" method="post">'
        '<input name="formhash" value="hash123"></form>'
    )
    login_page.final_url = "https://bbs.yamibo.com/member.php?mod=logging&action=login"
    login_page.status_code = 200
    login_page.url = login_page.final_url

    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.read.side_effect = TimeoutError("The read operation timed out")

    fake_opener = MagicMock()
    fake_opener.open.return_value = response
    client.opener = fake_opener
    client._open_html = MagicMock(return_value=login_page)

    with pytest.raises(RemoteFetchError, match="failed to read login response"):
        client._login(base_url="https://bbs.yamibo.com", referer="https://bbs.yamibo.com/")


def test_client_bootstraps_login_when_cookie_missing(tmp_path, monkeypatch):
    cookie_file = tmp_path / "missing.cookie"
    called = {}

    def fake_login(self, *, base_url: str, referer: str | None = None):
        called["base_url"] = base_url
        called["referer"] = referer
        self.cookie_jar.set_cookie(
            Cookie(
                version=0,
                name="session",
                value="fresh",
                port=None,
                port_specified=False,
                domain="bbs.yamibo.com",
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=False,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )
        self._save_cookies()

    monkeypatch.setattr(YamiboClient, "_login", fake_login)

    client = YamiboClient(cookie_file=str(cookie_file), login_username="u", login_password="p")

    assert called["base_url"] == "https://bbs.yamibo.com"
    assert called["referer"] == "https://bbs.yamibo.com/"
    assert cookie_file.read_text(encoding="utf-8").strip() == "session=fresh"
    assert list(client.cookie_jar)


def test_cookie_request_throttle_is_shared_across_clients(monkeypatch):
    runtime_limits._STATE_REGISTRY.clear()
    values = iter([100.0, 100.0, 100.1, 100.1])
    sleeps: list[float] = []

    monkeypatch.setattr(runtime_limits.time, "monotonic", lambda: next(values))
    monkeypatch.setattr(runtime_limits.time, "sleep", lambda value: sleeps.append(value))

    runtime_limits.throttle_cookie_request("/tmp/shared.cookie", request_interval=1.0, request_interval_jitter=0.0)
    runtime_limits.throttle_cookie_request("/tmp/shared.cookie", request_interval=1.0, request_interval_jitter=0.0)

    assert sleeps == [pytest.approx(0.9)]


def test_fetch_retries_remote_disconnected(monkeypatch):
    client = YamiboClient(timeout=0.1, retries=2)
    calls = {"count": 0}

    def _fake_open(_url: str, *, referer: str | None = None):
        calls["count"] += 1
        if calls["count"] < 3:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return FetchResult(
            url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1",
            final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1",
            status_code=200,
            html="<html></html>",
        )

    monkeypatch.setattr(client, "_open_html", _fake_open)

    result = client._fetch_with_validation(
        "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1",
        lambda _result: None,
    )

    assert result.status_code == 200
    assert calls["count"] == 3


def test_fetch_remote_disconnected_error_contains_details(monkeypatch):
    client = YamiboClient(timeout=0.1, retries=1)

    monkeypatch.setattr(
        client,
        "_open_html",
        lambda _url, *, referer=None: (_ for _ in ()).throw(http.client.RemoteDisconnected("Remote end closed connection without response")),
    )

    with pytest.raises(RemoteFetchError) as excinfo:
        client._fetch_with_validation(
            "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1",
            lambda _result: None,
        )

    assert excinfo.value.details["url"] == "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1"
    assert excinfo.value.details["attempts"] == 2
    assert excinfo.value.details["last_error_type"] == "RemoteDisconnected"


def test_validate_thread_page_reports_discuz_prompt_text():
    client = YamiboClient()
    result = FetchResult(
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        status_code=200,
        html=(
            '<html><body>'
            '<div id="messagetext" class="alert_info">查无此区，此区已关闭</div>'
            "</body></html>"
        ),
    )

    with pytest.raises(UnexpectedPageError) as excinfo:
        client._validate_thread_page(result)

    assert "prompt page" in str(excinfo.value)
    assert excinfo.value.details["url"] == result.final_url
    assert excinfo.value.details["page_type"] == "prompt_forum_closed"
    assert excinfo.value.details["prompt_text"] == "查无此区，此区已关闭"


def test_validate_thread_page_reports_removed_thread_prompt():
    client = YamiboClient()
    result = FetchResult(
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        status_code=200,
        html=(
            '<html><body>'
            '<div id="messagetext" class="alert_error">抱歉，指定的主题不存在或已被删除或正在被审核</div>'
            "</body></html>"
        ),
    )

    with pytest.raises(UnexpectedPageError) as excinfo:
        client._validate_thread_page(result)

    assert excinfo.value.details["page_type"] == "prompt_thread_missing_or_removed_or_review"
    assert excinfo.value.details["prompt_text"] == "抱歉，指定的主题不存在或已被删除或正在被审核"


def test_validate_thread_page_reports_permission_required_prompt():
    client = YamiboClient()
    result = FetchResult(
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        status_code=200,
        html=(
            '<html><body>'
            '<div id="messagetext" class="alert_error">抱歉，本帖要求阅读权限高于 30 才能浏览</div>'
            "</body></html>"
        ),
    )

    with pytest.raises(ThreadPermissionRequiredError) as excinfo:
        client._validate_thread_page(result)

    assert excinfo.value.required_permission == 30
    assert excinfo.value.details["page_type"] == "prompt_thread_permission_required"
    assert excinfo.value.details["prompt_text"] == "抱歉，本帖要求阅读权限高于 30 才能浏览"


def test_client_explicit_proxy_url_constructs_proxy_handler(monkeypatch):
    """When proxy_url is passed, opener uses explicit ProxyHandler."""
    build_opener_calls = []

    original_build_opener = urllib.request.build_opener

    def _fake_build_opener(*handlers):
        build_opener_calls.append(handlers)
        return original_build_opener(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", _fake_build_opener)

    YamiboClient(proxy_url="http://127.0.0.1:7890")

    proxy_handlers = [h for h in build_opener_calls[0] if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_client_no_proxy_url_uses_system_proxy_logic(monkeypatch):
    """Without proxy_url, behavior is unchanged: ProxyHandler({}) only when not use_system_proxy."""
    build_opener_calls = []

    def _fake_build_opener(*handlers):
        build_opener_calls.append(handlers)
        return MagicMock()

    monkeypatch.setattr(urllib.request, "build_opener", _fake_build_opener)

    # default: use_system_proxy=False
    YamiboClient()
    handlers1 = build_opener_calls[0]
    # should have ProxyHandler({}) to bypass system proxy
    proxy_handlers = [h for h in handlers1 if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}

    build_opener_calls.clear()

    # use_system_proxy=True
    YamiboClient(use_system_proxy=True)
    handlers2 = build_opener_calls[0]
    # should NOT have ProxyHandler({}) — lets system proxy through
    proxy_handlers2 = [h for h in handlers2 if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers2) == 0


def test_client_explicit_proxy_overrides_system_proxy(monkeypatch):
    """Explicit proxy_url overrides use_system_proxy."""
    build_opener_calls = []

    def _fake_build_opener(*handlers):
        build_opener_calls.append(handlers)
        return MagicMock()

    monkeypatch.setattr(urllib.request, "build_opener", _fake_build_opener)

    YamiboClient(proxy_url="http://127.0.0.1:7890", use_system_proxy=True)
    proxy_handlers = [h for h in build_opener_calls[0] if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_client_exposes_proxy_url():
    client = YamiboClient(proxy_url="http://127.0.0.1:7890")
    assert client.proxy_url == "http://127.0.0.1:7890"


def test_client_proxy_url_defaults_to_none():
    client = YamiboClient()
    assert client.proxy_url is None
