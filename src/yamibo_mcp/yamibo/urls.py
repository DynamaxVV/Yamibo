from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from yamibo_mcp.yamibo.parsers.common import extract_tid


DEFAULT_THREAD_BASE = "https://bbs.yamibo.com"
DEFAULT_COMIC_FORUM_ID = 30
DEFAULT_FORUM_ID = DEFAULT_COMIC_FORUM_ID


def stable_attachment_id(url: str) -> str | None:
    """Return the stable numeric component of a signed Yamibo attachment URL."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "bbs.yamibo.com" or parsed.path.lower() != "/forum.php":
        return None
    query = parse_qs(parsed.query)
    mod_values = {value.lower() for value in query.get("mod", [])}
    if "attachment" not in mod_values and "attachment/image" not in mod_values:
        return None
    encoded = unquote(query.get("aid", [""])[0]).strip()
    if not encoded:
        return None
    decoded = encoded
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        # Some fixtures and older exports contain the decoded aid directly.
        pass
    stable = decoded.split("|", 1)[0].strip()
    return stable or None


def remote_image_identity(url: str) -> tuple[str, str]:
    """Build an equality key that ignores only Yamibo attachment signatures."""
    attachment_id = stable_attachment_id(url)
    if attachment_id is not None:
        return ("yamibo_attachment", attachment_id)
    return ("url", url)


def thread_url_from_tid(tid: int, *, base_url: str = DEFAULT_THREAD_BASE) -> str:
    base = base_url.rstrip("/")
    return f"{base}/forum.php?mod=viewthread&tid={tid}"


def thread_author_url_from_tid(
    tid: int,
    *,
    author_uid: str,
    base_url: str = DEFAULT_THREAD_BASE,
) -> str:
    base = base_url.rstrip("/")
    return f"{base}/forum.php?mod=viewthread&tid={tid}&authorid={author_uid}"


def thread_page_url_from_tid(
    tid: int,
    *,
    page: int,
    author_uid: str | None = None,
    base_url: str = DEFAULT_THREAD_BASE,
) -> str:
    if page <= 0:
        raise ValueError(f"page must be positive: {page}")
    base = base_url.rstrip("/")
    query_items = [("mod", "viewthread"), ("tid", str(tid)), ("page", str(page))]
    if author_uid:
        query_items.append(("authorid", str(author_uid)))
    return f"{base}/forum.php?{urlencode(query_items)}"


def forum_page_url(page: int, *, forum_id: int = DEFAULT_FORUM_ID, base_url: str = DEFAULT_THREAD_BASE) -> str:
    if page <= 0:
        raise ValueError(f"page must be positive: {page}")
    base = base_url.rstrip("/")
    return f"{base}/forum-{forum_id}-{page}.html"


def dateline_forum_page_url(page: int, *, forum_id: int = DEFAULT_FORUM_ID, base_url: str = DEFAULT_THREAD_BASE) -> str:
    if page <= 0:
        raise ValueError(f"page must be positive: {page}")
    base = base_url.rstrip("/")
    return f"{base}/forum.php?mod=forumdisplay&fid={forum_id}&orderby=dateline&page={page}"


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
