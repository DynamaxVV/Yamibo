from __future__ import annotations

from types import SimpleNamespace

from yamibo_mcp.errors import RemoteFetchError
from yamibo_mcp.yamibo.browser_fallback import BrowserFallbackClient
from yamibo_mcp.yamibo.client import FetchResult, YamiboClient


def test_sign_daily_checkin_opens_page_then_action(monkeypatch):
    client = object.__new__(YamiboClient)
    calls = []

    def fake_open(url, *, referer=None):
        calls.append((url, referer))
        return FetchResult(url=url, final_url=url, status_code=200, html="<html></html>")

    monkeypatch.setattr(client, "_open_authenticated_page", fake_open)
    monkeypatch.setattr(client, "_save_cookies", lambda: calls.append("save"))

    result = client.sign_daily_checkin()

    assert result.final_url == YamiboClient.SIGN_IN_ACTION_URL
    assert calls == [
        (YamiboClient.SIGN_IN_PAGE_URL, "https://bbs.yamibo.com/"),
        (YamiboClient.SIGN_IN_ACTION_URL, YamiboClient.SIGN_IN_PAGE_URL),
        "save",
    ]


def test_browser_fallback_client_retries_sign_in_after_soft_block(monkeypatch, tmp_path):
    client = object.__new__(YamiboClient)
    settings = SimpleNamespace(data_dir=tmp_path, request_timeout_seconds=1)
    fallback_result = FetchResult(
        url=YamiboClient.SIGN_IN_ACTION_URL,
        final_url=YamiboClient.SIGN_IN_ACTION_URL,
        status_code=200,
        html="<html></html>",
    )
    calls = []

    def blocked_sign(**kwargs):
        calls.append(kwargs)
        raise RemoteFetchError("soft block detected")

    monkeypatch.setattr(client, "sign_daily_checkin", blocked_sign)
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.browser_fallback.sign_daily_checkin_with_browser",
        lambda **kwargs: calls.append(kwargs) or fallback_result,
    )
    wrapper = BrowserFallbackClient(
        client,
        settings=settings,
        account_id="secondary",
        username="u",
        password="p",
        proxy_url=None,
    )

    assert wrapper.sign_daily_checkin() == fallback_result
    assert len(calls) == 2
