from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from yamibo_mcp.errors import RemoteFetchError
from yamibo_mcp.yamibo.browser_fallback import BrowserFallbackClient
from yamibo_mcp.yamibo.client import FetchResult


def _wrapper(client):
    return BrowserFallbackClient(
        client,
        settings=SimpleNamespace(),
        account_id="primary",
        username="user",
        password="secret",
        proxy_url=None,
    )


def test_regular_forum_result_does_not_start_browser(monkeypatch):
    expected = (Mock(), [])
    client = Mock()
    client.fetch_forum_threads.return_value = expected
    browser_fetch = Mock()
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.browser_fallback.fetch_html_with_browser", browser_fetch
    )

    assert _wrapper(client).fetch_forum_threads(page=1, forum_id=30) == expected
    browser_fetch.assert_not_called()


def test_soft_block_forum_result_uses_browser(monkeypatch):
    client = Mock()
    client.fetch_forum_threads.side_effect = RemoteFetchError("soft block detected")
    client._validate_forum_page = Mock()
    result = FetchResult(
        url="https://bbs.yamibo.com/forum-30-1.html",
        final_url="https://bbs.yamibo.com/forum-30-1.html",
        status_code=200,
        html='<html><body><div id="threadlisttableid"></div></body></html>',
    )
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.browser_fallback.fetch_html_with_browser",
        lambda **_kwargs: result,
    )

    fetched, items = _wrapper(client).fetch_forum_threads(page=1, forum_id=30)

    assert fetched == result
    assert items == []
    client._validate_forum_page.assert_called_once_with(result)


def test_non_waf_fetch_error_is_not_hidden(monkeypatch):
    client = Mock()
    error = RemoteFetchError("upstream timed out")
    client.fetch_thread_page.side_effect = error
    browser_fetch = Mock()
    monkeypatch.setattr(
        "yamibo_mcp.yamibo.browser_fallback.fetch_html_with_browser", browser_fetch
    )

    with pytest.raises(RemoteFetchError) as raised:
        _wrapper(client).fetch_thread_page(tid=123, page=1)

    assert raised.value is error
    browser_fetch.assert_not_called()
