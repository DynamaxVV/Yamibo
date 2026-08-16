from __future__ import annotations

from types import SimpleNamespace

from yamibo_mcp.errors import RemoteFetchError
from yamibo_mcp.yamibo.browser_fallback import BrowserFallbackClient
from yamibo_mcp.yamibo.client import (
    FetchResult,
    YamiboClient,
    daily_checkin_already_done,
    daily_checkin_action_url,
    validate_daily_checkin_result,
)


_AUTHENTICATED_HTML = """
<script>var discuz_uid = '123';</script>
<a href="member.php?mod=logging&amp;action=logout">退出</a>
"""


def test_sign_daily_checkin_opens_page_then_action(monkeypatch):
    client = object.__new__(YamiboClient)
    calls = []

    def fake_open(url, *, referer=None):
        calls.append((url, referer))
        return FetchResult(url=url, final_url=url, status_code=200, html=_AUTHENTICATED_HTML + "恭喜您，打卡成功！")

    monkeypatch.setattr(client, "_open_authenticated_page", fake_open)
    monkeypatch.setattr(client, "_save_cookies", lambda: calls.append("save"))

    result = client.sign_daily_checkin()

    assert result.final_url == YamiboClient.SIGN_IN_ACTION_URL
    assert calls == [
        (YamiboClient.SIGN_IN_PAGE_URL, "https://bbs.yamibo.com/"),
        (YamiboClient.SIGN_IN_ACTION_URL, YamiboClient.SIGN_IN_PAGE_URL),
        "save",
    ]


def test_validate_daily_checkin_accepts_already_checked_page():
    result = FetchResult(
        url=YamiboClient.SIGN_IN_PAGE_URL,
        final_url=YamiboClient.SIGN_IN_PAGE_URL,
        status_code=200,
        html=_AUTHENTICATED_HTML + '<a href="plugin.php?id=zqlj_sign">今日已打卡</a>',
    )

    assert validate_daily_checkin_result(result) == result


def test_daily_checkin_status_ignores_other_users_record_table():
    html = """
    <a href="plugin.php?id=zqlj_sign"><font>签到</font></a>
    <table><tr><td><font color="green">今日已打卡</font></td></tr></table>
    """

    assert daily_checkin_already_done(html) is False


def test_daily_checkin_status_reads_current_account_link():
    html = '<a href="plugin.php?id=zqlj_sign"><font>今日已打卡</font></a>'

    assert daily_checkin_already_done(html) is True


def test_daily_checkin_action_url_uses_current_page_formhash():
    html = '<a href="plugin.php?id=zqlj_sign&sign=current-formhash">点击打卡</a>'

    assert daily_checkin_action_url(
        html,
        base_url=YamiboClient.SIGN_IN_PAGE_URL,
        fallback_url=YamiboClient.SIGN_IN_ACTION_URL,
    ) == "https://bbs.yamibo.com/plugin.php?id=zqlj_sign&sign=current-formhash"


def test_sign_daily_checkin_does_not_click_when_already_checked(monkeypatch):
    client = object.__new__(YamiboClient)
    calls = []

    def fake_open(url, *, referer=None):
        calls.append((url, referer))
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            html=_AUTHENTICATED_HTML + '<a href="plugin.php?id=zqlj_sign">今日已打卡</a>',
        )

    monkeypatch.setattr(client, "_open_authenticated_page", fake_open)
    monkeypatch.setattr(client, "_save_cookies", lambda: calls.append("save"))

    result = client.sign_daily_checkin()

    assert result.final_url == YamiboClient.SIGN_IN_PAGE_URL
    assert calls == [(YamiboClient.SIGN_IN_PAGE_URL, "https://bbs.yamibo.com/"), "save"]


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


def test_browser_fallback_client_retries_sign_in_after_http2_transport_error(monkeypatch, tmp_path):
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
        raise RuntimeError("curl: (92) HTTP/2 stream 1 was not closed cleanly")

    monkeypatch.setattr(client, "sign_daily_checkin", blocked_sign)
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.browser_fallback.sign_daily_checkin_with_browser",
        lambda **kwargs: calls.append(kwargs) or fallback_result,
    )
    wrapper = BrowserFallbackClient(
        client,
        settings=settings,
        account_id="tertiary",
        username="u",
        password="p",
        proxy_url=None,
    )

    assert wrapper.sign_daily_checkin() == fallback_result
    assert len(calls) == 2
