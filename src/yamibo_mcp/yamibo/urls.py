from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from yamibo_mcp.yamibo.parsers.common import extract_tid


DEFAULT_THREAD_BASE = "https://bbs.yamibo.com"
DEFAULT_COMIC_FORUM_ID = 30
DEFAULT_FORUM_ID = DEFAULT_COMIC_FORUM_ID


def thread_url_from_tid(tid: int, *, base_url: str = DEFAULT_THREAD_BASE) -> str:
    base = base_url.rstrip("/")
    return f"{base}/forum.php?mod=viewthread&tid={tid}"


def forum_page_url(page: int, *, forum_id: int = DEFAULT_FORUM_ID, base_url: str = DEFAULT_THREAD_BASE) -> str:
    if page <= 0:
        raise ValueError(f"page must be positive: {page}")
    base = base_url.rstrip("/")
    return f"{base}/forum-{forum_id}-{page}.html"


def extract_tid_from_input(value: str | int) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return extract_tid(text)


def normalize_thread_url(value: str, *, base_url: str = DEFAULT_THREAD_BASE) -> str:
    tid = extract_tid_from_input(value)
    if tid is None:
        raise ValueError(f"cannot extract tid from input: {value}")

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["mod"] = ["viewthread"]
        query["tid"] = [str(tid)]
        normalized_query = urlencode([(key, item) for key, values in query.items() for item in values])
        # 统一成 forum.php?mod=viewthread&tid=... 这种形式，避免同一帖子出现多个 URL 版本。
        return urlunparse((parsed.scheme, parsed.netloc, "/forum.php", "", normalized_query, ""))

    return thread_url_from_tid(tid, base_url=base_url)


def normalize_forum_page_url(
    value: str | int,
    *,
    forum_id: int = DEFAULT_FORUM_ID,
    base_url: str = DEFAULT_THREAD_BASE,
) -> str:
    if isinstance(value, int):
        return forum_page_url(value, forum_id=forum_id, base_url=base_url)

    text = str(value).strip()
    if text.isdigit():
        return forum_page_url(int(text), forum_id=forum_id, base_url=base_url)

    direct_match = re.search(r"forum-(\d+)-(\d+)\.html", text)
    if direct_match:
        found_forum_id = int(direct_match.group(1))
        page = int(direct_match.group(2))
        return forum_page_url(page, forum_id=found_forum_id, base_url=base_url)

    parsed = urlparse(text)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("mod", [""])[0] == "forumdisplay":
        page = int(query.get("page", ["1"])[0])
        found_forum_id = int(query.get("fid", [str(forum_id)])[0])
        return forum_page_url(page, forum_id=found_forum_id, base_url=base_url)

    raise ValueError(f"cannot normalize forum page url: {value}")
