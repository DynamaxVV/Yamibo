from __future__ import annotations

import pytest

from yamibo_mcp.yamibo.urls import (
    DEFAULT_COMIC_FORUM_ID,
    DEFAULT_FORUM_ID,
    forum_page_url,
    thread_author_url_from_tid,
    thread_page_url_from_tid,
)


class TestForumPageUrl:
    def test_default_forum_id_points_to_forum_30(self):
        url = forum_page_url(1)
        assert url.endswith("/forum-30-1.html")

    def test_explicit_forum_id_30(self):
        url = forum_page_url(1, forum_id=30)
        assert url.endswith("/forum-30-1.html")

    def test_explicit_forum_id_55(self):
        url = forum_page_url(1, forum_id=55)
        assert url.endswith("/forum-55-1.html")

    def test_explicit_forum_id_5(self):
        url = forum_page_url(1, forum_id=5)
        assert url.endswith("/forum-5-1.html")

    def test_explicit_forum_id_33(self):
        url = forum_page_url(3, forum_id=33)
        assert url.endswith("/forum-33-3.html")

    def test_custom_base_url(self):
        url = forum_page_url(1, forum_id=55, base_url="https://example.com")
        assert url == "https://example.com/forum-55-1.html"

    def test_page_must_be_positive(self):
        with pytest.raises(ValueError):
            forum_page_url(0)

    def test_negative_page_raises(self):
        with pytest.raises(ValueError):
            forum_page_url(-1)


class TestDefaultForumIdAliases:
    def test_both_aliases_exist_and_equal(self):
        assert DEFAULT_FORUM_ID == DEFAULT_COMIC_FORUM_ID == 30


class TestThreadPageUrl:
    def test_author_only_thread_root_url(self):
        url = thread_author_url_from_tid(540745, author_uid="229047")
        assert url == "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=540745&authorid=229047"

    def test_basic_thread_page(self):
        url = thread_page_url_from_tid(521519, page=1)
        assert url == "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=521519&page=1"

    def test_author_only_thread_page(self):
        url = thread_page_url_from_tid(540745, page=1, author_uid="229047")
        assert url == "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=540745&page=1&authorid=229047"

    def test_invalid_page_raises(self):
        with pytest.raises(ValueError):
            thread_page_url_from_tid(1, page=0)
