from __future__ import annotations

from yamibo_mcp.domain.forums import (
    DEFAULT_COMIC_FORUM_ID,
    DEFAULT_FORUM_ID,
    ForumProfile,
    default_forums,
    resolve_forum,
)


class TestForumProfile:
    def test_default_values(self):
        # Arrange & Act
        profile = ForumProfile(forum_id=30, name="comic", content_kind="comic")
        # Assert
        assert profile.base_url == "https://bbs.yamibo.com"
        assert profile.enabled is True

    def test_frozen(self):
        # Arrange
        profile = ForumProfile(forum_id=30, name="comic", content_kind="comic")
        # Act & Assert
        import pytest
        with pytest.raises(AttributeError):
            profile.forum_id = 55


class TestDefaultForumId:
    def test_default_forum_id_is_30(self):
        assert DEFAULT_FORUM_ID == 30

    def test_default_comic_forum_id_is_30(self):
        assert DEFAULT_COMIC_FORUM_ID == 30

    def test_both_aliases_equal(self):
        assert DEFAULT_FORUM_ID == DEFAULT_COMIC_FORUM_ID


class TestDefaultForums:
    def test_returns_four_profiles(self):
        forums = default_forums()
        assert len(forums) == 4

    def test_contains_comic_forum(self):
        forums = default_forums()
        comic = next(f for f in forums if f.forum_id == 30)
        assert comic.name == "comic"
        assert comic.content_kind == "comic"

    def test_contains_novel_forum(self):
        forums = default_forums()
        novel = next(f for f in forums if f.forum_id == 55)
        assert novel.name == "novel"
        assert novel.content_kind == "novel"

    def test_contains_anime_forum(self):
        forums = default_forums()
        anime = next(f for f in forums if f.forum_id == 5)
        assert anime.name == "anime"
        assert anime.content_kind == "discussion"

    def test_contains_discussion_forum(self):
        forums = default_forums()
        disc = next(f for f in forums if f.forum_id == 33)
        assert disc.name == "discussion"
        assert disc.content_kind == "discussion"


class TestResolveForum:
    def test_none_returns_default(self):
        profile = resolve_forum(None)
        assert profile.forum_id == 30
        assert profile.name == "comic"

    def test_explicit_30(self):
        profile = resolve_forum(30)
        assert profile.forum_id == 30
        assert profile.content_kind == "comic"

    def test_explicit_55(self):
        profile = resolve_forum(55)
        assert profile.forum_id == 55
        assert profile.content_kind == "novel"

    def test_unknown_forum_returns_generic(self):
        profile = resolve_forum(999)
        assert profile.forum_id == 999
        assert profile.name == "forum-999"
        assert profile.content_kind == "unknown"
