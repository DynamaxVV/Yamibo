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


def default_forums() -> list[ForumProfile]:
    return [
        ForumProfile(forum_id=30, name="comic", content_kind="comic"),
        ForumProfile(forum_id=55, name="novel", content_kind="novel"),
        ForumProfile(forum_id=5, name="anime", content_kind="discussion"),
        ForumProfile(forum_id=33, name="discussion", content_kind="discussion"),
    ]


def resolve_forum(forum_id: int | None) -> ForumProfile:
    if forum_id is None:
        forum_id = DEFAULT_FORUM_ID
    for profile in default_forums():
        if profile.forum_id == forum_id:
            return profile
    return ForumProfile(forum_id=forum_id, name=f"forum-{forum_id}", content_kind="unknown")
