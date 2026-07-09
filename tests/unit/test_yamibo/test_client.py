from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import curl_cffi.requests.errors as curl_errors

from yamibo_mcp.errors import RemoteFetchError, ThreadPermissionRequiredError, UnexpectedPageError
from yamibo_mcp.yamibo.client import BurstThrottle, FetchResult, YamiboClient
from yamibo_mcp.yamibo import runtime_limits


class _FakeResponse:
    def __init__(self, *, url: str = "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1",
                 content: bytes = b"<html></html>", status_code: int = 200,
                 headers: dict | None = None) -> None:
        self.url = url
        self._content = content
        self.status_code = status_code
        self.headers = headers or {}

    @property
    def content(self) -> bytes:
        return self._content


class _ReadTimeoutResponse(_FakeResponse):
    @property
    def content(self) -> bytes:  # type: ignore[override]
        raise TimeoutError("The read operation timed out")


class _FakeSession:
    def __init__(self, *, get_response=None, post_response=None):
        self._get = get_response
        self._post = post_response
        self.cookies = MagicMock()
        self.proxies = {}

    def get(self, url, *, headers=None, timeout=None):
        if self._get is None:
            return _FakeResponse(url=url)
        return self._get

    def post(self, url, *, data=None, headers=None, timeout=None):
        if self._post is None:
            return _FakeResponse(url=url)
        return self._post

    def close(self):
        pass


def test_fetch_url_wraps_read_timeout_as_remote_fetch_error():
    client = YamiboClient(timeout=0.1, retries=0)
    client._session = _FakeSession(get_response=_ReadTimeoutResponse())  # type: ignore[arg-type]
    with pytest.raises(RemoteFetchError, match="failed to fetch"):
        client.fetch_url("https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1")


def test_login_read_timeout_wraps_as_remote_fetch_error():
    client = YamiboClient(timeout=0.1, retries=0, login_username="u", login_password="p")
    login_page = FetchResult(
        url="https://bbs.yamibo.com/member.php?mod=logging&action=login",
        final_url="https://bbs.yamibo.com/member.php?mod=logging&action=login",
        status_code=200,
        html=('<form action="member.php?mod=logging&action=login&loginsubmit=yes" method="post">'
              '<input name="formhash" value="hash123"></form>'),
    )
    client._open_html = MagicMock(return_value=login_page)
    client._session = _FakeSession(post_response=_ReadTimeoutResponse())  # type: ignore[arg-type]
    with pytest.raises(RemoteFetchError, match="failed to read login response"):
        client._login(base_url="https://bbs.yamibo.com", referer="https://bbs.yamibo.com/")


def test_client_bootstraps_login_when_cookie_missing(tmp_path, monkeypatch):
    cookie_file = tmp_path / "missing.cookie"
    called = {}

    def fake_login(self, *, base_url: str, referer: str | None = None):
        called["base_url"] = base_url
        called["referer"] = referer
        cookie_file.write_text("session=fresh", encoding="utf-8")

    monkeypatch.setattr(YamiboClient, "_login", fake_login)
    client = YamiboClient(cookie_file=str(cookie_file), login_username="u", login_password="p")
    assert called["base_url"] == "https://bbs.yamibo.com"
    assert called["referer"] == "https://bbs.yamibo.com/"
    assert cookie_file.read_text(encoding="utf-8").strip() == "session=fresh"


def test_cookie_request_throttle_is_shared_across_clients(monkeypatch):
    runtime_limits._STATE_REGISTRY.clear()
    values = iter([100.0, 100.1])
    sleeps: list[float] = []

    monkeypatch.setattr(runtime_limits.time, "monotonic", lambda: next(values))
    monkeypatch.setattr(runtime_limits.time, "sleep", lambda value: sleeps.append(value))

    runtime_limits.throttle_cookie_request("/tmp/shared.cookie", request_interval=1.0, request_interval_jitter=0.0)
    runtime_limits.throttle_cookie_request("/tmp/shared.cookie", request_interval=1.0, request_interval_jitter=0.0)
    assert sleeps == [pytest.approx(0.9)]


def test_cookie_request_throttle_sleeps_outside_cookie_lock(monkeypatch):
    runtime_limits._STATE_REGISTRY.clear()
    key = "/tmp/shared-lock.cookie"
    state = runtime_limits._state_for(key)
    values = iter([100.0, 100.1])
    sleeps: list[float] = []

    monkeypatch.setattr(runtime_limits.time, "monotonic", lambda: next(values))

    def _sleep(value: float) -> None:
        assert not state.lock.locked()
        sleeps.append(value)

    monkeypatch.setattr(runtime_limits.time, "sleep", _sleep)

    runtime_limits.throttle_cookie_request(key, request_interval=1.0, request_interval_jitter=0.0)
    runtime_limits.throttle_cookie_request(key, request_interval=1.0, request_interval_jitter=0.0)

    assert sleeps == [pytest.approx(0.9)]


def test_fetch_retries_curl_error(monkeypatch):
    client = YamiboClient(timeout=0.1, retries=2)
    calls = {"count": 0}

    def _fake_open(_url: str, *, referer: str | None = None):
        calls["count"] += 1
        if calls["count"] < 3:
            raise curl_errors.RequestsError("Remote end closed connection without response")
        return FetchResult(
            url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1",
            final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1",
            status_code=200, html="<html></html>",
        )

    monkeypatch.setattr(client, "_open_html", _fake_open)
    result = client._fetch_with_validation(
        "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1", lambda _result: None,
    )
    assert result.status_code == 200
    assert calls["count"] == 3


def test_fetch_curl_error_contains_details(monkeypatch):
    client = YamiboClient(timeout=0.1, retries=1)
    monkeypatch.setattr(client, "_open_html",
        lambda _url, *, referer=None: (_ for _ in ()).throw(
            curl_errors.RequestsError("Remote end closed connection without response")))
    with pytest.raises(RemoteFetchError) as excinfo:
        client._fetch_with_validation(
            "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1", lambda _result: None,
        )
    assert excinfo.value.details["url"] == "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=1"
    assert excinfo.value.details["attempts"] == 2
    assert excinfo.value.details["last_error_type"] == "RequestException"


def test_validate_thread_page_reports_discuz_prompt_text():
    client = YamiboClient()
    result = FetchResult(
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        status_code=200,
        html=('<html><body>'
              '<div id="messagetext" class="alert_info">查无此区，此区已关闭</div>'
              "</body></html>"),
    )
    with pytest.raises(UnexpectedPageError) as excinfo:
        client._validate_thread_page(result)
    assert "prompt page" in str(excinfo.value)
    assert excinfo.value.details["page_type"] == "prompt_forum_closed"


def test_validate_thread_page_reports_removed_thread_prompt():
    client = YamiboClient()
    result = FetchResult(
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        status_code=200,
        html=('<html><body>'
              '<div id="messagetext" class="alert_error">抱歉，指定的主题不存在或已被删除或正在被审核</div>'
              "</body></html>"),
    )
    with pytest.raises(UnexpectedPageError) as excinfo:
        client._validate_thread_page(result)
    assert excinfo.value.details["page_type"] == "prompt_thread_missing_or_removed_or_review"


def test_validate_thread_page_reports_permission_required_prompt():
    client = YamiboClient()
    result = FetchResult(
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        final_url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=129",
        status_code=200,
        html=('<html><body>'
              '<div id="messagetext" class="alert_error">抱歉，本帖要求阅读权限高于 30 才能浏览</div>'
              "</body></html>"),
    )
    with pytest.raises(ThreadPermissionRequiredError) as excinfo:
        client._validate_thread_page(result)
    assert excinfo.value.required_permission == 30


def test_client_explicit_proxy_constructs_proxies_dict():
    client = YamiboClient(proxy_url="http://127.0.0.1:7890")
    assert client._session.proxies == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_client_no_proxy_bypasses_system():
    client = YamiboClient()
    assert client._session.proxies == {"http": "", "https": ""}


def test_client_system_proxy_leaves_empty():
    client = YamiboClient(use_system_proxy=True)
    assert client._session.proxies == {}


def test_client_proxy_overrides_system():
    client = YamiboClient(proxy_url="http://127.0.0.1:7890", use_system_proxy=True)
    assert client._session.proxies == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_client_exposes_proxy_url():
    assert YamiboClient(proxy_url="http://127.0.0.1:7890").proxy_url == "http://127.0.0.1:7890"


def test_client_proxy_url_defaults_to_none():
    assert YamiboClient().proxy_url is None


def test_burst_throttle_has_long_pause():
    sleeps = []
    bt = BurstThrottle()
    import time as _time
    orig = _time.sleep
    _time.sleep = sleeps.append
    try:
        for _ in range(10):
            bt.wait()
    finally:
        _time.sleep = orig
    assert any(s > 1.5 for s in sleeps), f"Expected a long pause in {sleeps}"
