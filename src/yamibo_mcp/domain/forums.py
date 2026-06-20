from __future__ import annotations

from dataclasses import dataclass


DEFAULT_FORUM_ID = 30
DEFAULT_COMIC_FORUM_ID = DEFAULT_FORUM_ID


@dataclass(frozen=True)
class ForumProfile:
    forum_id: int
    name: str
    content_kind: str
    base_url: str = "https://bbs.yamibo.com"
    enabled: bool = True
    default_series_key: str | None = None
    default_series_title: str | None = None
    name_en: str | None = None
    default_series_title_en: str | None = None


def default_forums() -> list[ForumProfile]:
    return [
        ForumProfile(forum_id=30, name="漫画区", content_kind="comic", name_en="Comic"),
        ForumProfile(forum_id=55, name="轻小说区", content_kind="novel", name_en="Novel"),
        ForumProfile(forum_id=5, name="动漫区", content_kind="discussion",
                     default_series_key="forum_anime", default_series_title="动漫区", name_en="Anime",
                     default_series_title_en="Anime"),
        ForumProfile(forum_id=33, name="海域区", content_kind="discussion",
                     default_series_key="forum_sea", default_series_title="海域区", name_en="Watercooler",
                     default_series_title_en="Watercooler"),
        ForumProfile(forum_id=13, name="贴图区", content_kind="discussion", name_en="Image Board"),
        ForumProfile(forum_id=16, name="管理版", content_kind="discussion", name_en="Admin"),
        ForumProfile(forum_id=19, name="资源交流区", content_kind="discussion", name_en="Resources"),
        ForumProfile(forum_id=44, name="游戏区", content_kind="discussion", name_en="Games"),
        ForumProfile(forum_id=49, name="文学区", content_kind="discussion", name_en="Literature"),
        ForumProfile(forum_id=370, name="使用指南", content_kind="discussion", name_en="Guide"),
        ForumProfile(forum_id=379, name="影视区", content_kind="discussion", name_en="Film & TV"),
    ]


def resolve_forum(forum_id: int | None) -> ForumProfile:
    if forum_id is None:
        forum_id = DEFAULT_FORUM_ID
    for profile in default_forums():
        if profile.forum_id == forum_id:
            return profile
    return ForumProfile(forum_id=forum_id, name=f"forum-{forum_id}", content_kind="unknown")
