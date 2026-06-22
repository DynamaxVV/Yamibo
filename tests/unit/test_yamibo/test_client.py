from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yamibo_mcp.errors import RemoteFetchError
from yamibo_mcp.yamibo.client import YamiboClient


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
