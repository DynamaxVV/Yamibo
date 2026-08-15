"""444 全局暂停恢复探测的单元测试。"""

from __future__ import annotations

import sqlite3

import pytest

from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.errors import RemoteFetchError
from yamibo_mcp.yamibo import anti_bot as ab


# 一个带 forum list 标记的极简 HTML，classify_html 会判为 FORUM_LIST
_NORMAL_FORUM_HTML = '<html><body><div id="threadlisttableid"></div></body></html>'
# 维护页 HTML
_MAINTENANCE_HTML = '<html><body>百合会每日维护</body></html>'
_BAIDU_WAF_HTML = (
    '<script>window.__noxExpire=30;</script>'
    '<script src="/static/gangplank_20251103.js"></script>'
)


def _open_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def _activate_pause(conn):
    """激活一次 444 暂停，便于探测测试。"""
    ab.activate_remote_access_pause(
        conn,
        source="test",
        message="test pause",
        context={},
    )


def test_probe_remote_access_success_clears_pause(monkeypatch):
    """探测拿到正常论坛页 → 返回 True，暂停被清除。"""
    conn = _open_db()
    _activate_pause(conn)
    try:
        from yamibo_mcp.yamibo.client import YamiboClient
        from yamibo_mcp.yamibo.urls import forum_page_url, DEFAULT_FORUM_ID

        captured = {}

        class _Result:
            html = _NORMAL_FORUM_HTML
            final_url = forum_page_url(page=1, forum_id=DEFAULT_FORUM_ID)

        def _fake_open_html(self, url, referer=None):
            captured["url"] = url
            return _Result()

        monkeypatch.setattr(YamiboClient, "_open_html", _fake_open_html)
        monkeypatch.setattr(YamiboClient, "_bootstrap_login_if_needed", lambda self: None)

        assert ab.probe_remote_access(cookie_file=None, settings=None) is True
        assert "yamibo.com" in captured["url"]

        # 探测成功后记录 → 清除暂停
        ab.record_remote_access_probe_success(conn)
        assert ab.get_remote_access_pause_state(conn) is None
    finally:
        conn.close()


def test_probe_remote_access_444_still_blocked(monkeypatch):
    """探测仍返回 444 → 返回 False，暂停保留。"""
    conn = _open_db()
    _activate_pause(conn)
    try:
        from yamibo_mcp.yamibo.client import YamiboClient

        def _fake_open_html(self, url, referer=None):
            raise RemoteFetchError("HTTP Error 444", details={"status_code": 444})

        monkeypatch.setattr(YamiboClient, "_open_html", _fake_open_html)
        monkeypatch.setattr(YamiboClient, "_bootstrap_login_if_needed", lambda self: None)

        assert ab.probe_remote_access(cookie_file=None, settings=None) is False
        ab.record_remote_access_probe_failure(conn)
        assert ab.get_remote_access_pause_state(conn) is not None
        state = ab.get_remote_access_pause_state(conn)
        assert state["probe_failures"] == 1
    finally:
        conn.close()


def test_probe_remote_access_maintenance_page_still_blocked(monkeypatch):
    """探测拿到维护页 → 不算正常，返回 False。"""
    conn = _open_db()
    _activate_pause(conn)
    try:
        from yamibo_mcp.yamibo.client import YamiboClient

        class _Result:
            html = _MAINTENANCE_HTML
            final_url = "http://x"

        monkeypatch.setattr(YamiboClient, "_open_html", lambda self, url, referer=None: _Result())
        monkeypatch.setattr(YamiboClient, "_bootstrap_login_if_needed", lambda self: None)

        assert ab.probe_remote_access(cookie_file=None, settings=None) is False
    finally:
        conn.close()


def test_probe_remote_access_network_error_still_blocked(monkeypatch):
    """探测遇到非 444 异常（网络错误）→ 保守判为仍被封，返回 False。"""
    conn = _open_db()
    _activate_pause(conn)
    try:
        from yamibo_mcp.yamibo.client import YamiboClient

        monkeypatch.setattr(
            YamiboClient, "_open_html",
            lambda self, url, referer=None: (_ for _ in ()).throw(ConnectionError("timeout")),
        )
        monkeypatch.setattr(YamiboClient, "_bootstrap_login_if_needed", lambda self: None)

        assert ab.probe_remote_access(cookie_file=None, settings=None) is False
    finally:
        conn.close()


def test_baidu_waf_page_is_detected_as_soft_block():
    """Baidu WAF challenge pages must not be classified as normal HTML."""
    assert ab.is_soft_block_page(_BAIDU_WAF_HTML) is True


def test_should_probe_remote_access_first_time_true():
    """刚激活暂停（last_probe_at=None）→ 首次探测应返回 True。"""
    conn = _open_db()
    _activate_pause(conn)
    try:
        assert ab.should_probe_remote_access(conn) is True
    finally:
        conn.close()


def test_should_probe_remote_access_false_within_interval():
    """刚探测失败（last_probe_at 设为现在）→ 10 分钟内不应再探测。"""
    conn = _open_db()
    _activate_pause(conn)
    try:
        ab.record_remote_access_probe_failure(conn)
        assert ab.should_probe_remote_access(conn) is False
    finally:
        conn.close()


def test_should_probe_remote_access_true_when_no_pause():
    """没有暂停状态 → 不需要探测，返回 False。"""
    conn = _open_db()
    try:
        assert ab.should_probe_remote_access(conn) is False
    finally:
        conn.close()
