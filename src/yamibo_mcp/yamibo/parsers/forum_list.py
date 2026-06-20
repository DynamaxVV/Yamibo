from __future__ import annotations

import html
import re
from dataclasses import dataclass

from yamibo_mcp.yamibo.parsers.common import extract_tid
from yamibo_mcp.yamibo.title.normalizer import normalize_display_title


@dataclass(frozen=True)
class ForumThreadItem:
    tid: int
    title: str
    category: str | None
    row_kind: str = "normal"
    publisher: str | None = None
    posted_at: str | None = None
    last_reply_at: str | None = None
    reply_count: int | None = None
    url: str | None = None

    @property
    def is_sticky(self) -> bool:
        return self.row_kind == "sticky"


ROW_RE = re.compile(r'<tbody id="(?P<row_id>(?:stickthread|normalthread)_\d+)">(?P<body>.*?)</tbody>', re.S)
TITLE_RE = re.compile(r'<a [^>]*href="(?P<href>[^"]*thread[^"]*)"[^>]*class="s xst"[^>]*>(?P<title>.*?)</a>', re.S)
CATEGORY_RE = re.compile(r'\[<a [^>]*filter=typeid[^>]*>(?P<category>.*?)</a>\]', re.S)
BY_BLOCK_RE = re.compile(r'<td class="by">(.*?)</td>', re.S)
NUM_BLOCK_RE = re.compile(r'<td class="num">(.*?)</td>', re.S)
ANCHOR_TEXT_RE = re.compile(r'<a [^>]*>(.*?)</a>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
DATETIME_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?")


def _strip_html(value: str) -> str:
    text = TAG_RE.sub("", value)
    return normalize_display_title(html.unescape(text))


def _extract_datetime(value: str) -> str | None:
    match = DATETIME_RE.search(html.unescape(value))
    return match.group(0) if match else None


def _extract_anchor_text(value: str) -> str | None:
    match = ANCHOR_TEXT_RE.search(value)
    if not match:
        return None
    text = _strip_html(match.group(1))
    return text or None


def _extract_counts(value: str) -> tuple[int | None, int | None]:
    numbers = re.findall(r">(\d+)<", value)
    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])
    if len(numbers) == 1:
        return int(numbers[0]), None
    return None, None


def parse_forum_list(html_text: str) -> list[ForumThreadItem]:
    items: list[ForumThreadItem] = []
    for match in ROW_RE.finditer(html_text):
        row_id = match.group("row_id")
        body = match.group("body")
        title_match = TITLE_RE.search(body)
        if not title_match:
            continue

        href = html.unescape(title_match.group("href"))
        title = _strip_html(title_match.group("title"))
        tid = extract_tid(href)
        if tid is None or not title:
            continue

        category_match = CATEGORY_RE.search(body)
        category = _strip_html(category_match.group("category")) if category_match else None
        by_blocks = BY_BLOCK_RE.findall(body)
        publisher = _extract_anchor_text(by_blocks[0]) if by_blocks else None
        posted_at = _extract_datetime(by_blocks[0]) if by_blocks else None
        last_reply_at = _extract_datetime(by_blocks[1]) if len(by_blocks) > 1 else None
        num_block = NUM_BLOCK_RE.search(body)
        reply_count, _view_count = _extract_counts(num_block.group(1)) if num_block else (None, None)

        items.append(
            ForumThreadItem(
                tid=tid,
                title=title,
                category=category,
                row_kind="sticky" if row_id.startswith("stickthread_") else "normal",
                publisher=publisher,
                posted_at=posted_at,
                last_reply_at=last_reply_at,
                reply_count=reply_count,
                url=href,
            )
        )
    return items


_TOTAL_PAGES_RE = re.compile(r'(?:共\s*(\d+)\s*页|<a[^>]*class="last"[^>]*>(\d+)</a>)')


def extract_total_pages(html_text: str) -> int:
    match = _TOTAL_PAGES_RE.search(html_text)
    if not match:
        return 1
    pages_str = match.group(1) or match.group(2)
    return int(pages_str) if pages_str else 1
