from __future__ import annotations

import html
import re
from dataclasses import dataclass

from yamibo_mcp.yamibo.parsers.common import extract_tid
from yamibo_mcp.yamibo.title.normalizer import normalize_display_title


@dataclass(frozen=True)
class SearchResultItem:
    tid: int
    title: str
    publisher: str | None = None
    posted_at: str | None = None
    category: str | None = None
    reply_count: int | None = None
    excerpt: str | None = None
    url: str | None = None


ITEM_RE = re.compile(r'<li class="pbw" id="(?P<tid>\d+)">(?P<body>.*?)</li>', re.S)
TITLE_RE = re.compile(r'<h3 class="xs3">\s*<a href="(?P<href>[^"]*mod=viewthread[^"]*)"[^>]*>(?P<title>.*?)</a>', re.S)
META_RE = re.compile(r'<p class="xg1">(?P<reply>\d+)\s*个回复', re.S)
EXCERPT_RE = re.compile(r'<p>(?P<excerpt>.*?)</p>\s*<p>\s*<span>', re.S)
DATE_RE = re.compile(r"<span>(?P<date>\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})</span>")
PUBLISHER_RE = re.compile(r'<span>\s*<a [^>]*space-uid-\d+\.html[^>]*>(?P<publisher>.*?)</a>\s*</span>', re.S)
CATEGORY_RE = re.compile(r'<span><a [^>]*forum-\d+-\d+\.html[^>]*class="xi1"[^>]*>(?P<category>.*?)</a></span>', re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(value: str) -> str:
    text = TAG_RE.sub("", value)
    return normalize_display_title(html.unescape(text))


def parse_search_results(html_text: str) -> list[SearchResultItem]:
    items: list[SearchResultItem] = []
    for match in ITEM_RE.finditer(html_text):
        body = match.group("body")
        title_match = TITLE_RE.search(body)
        if not title_match:
            continue
        href = html.unescape(title_match.group("href"))
        title = _strip_html(title_match.group("title"))
        tid = extract_tid(href) or int(match.group("tid"))
        if tid is None or not title:
            continue
        meta_match = META_RE.search(body)
        excerpt_match = EXCERPT_RE.search(body)
        date_match = DATE_RE.search(body)
        publisher_match = PUBLISHER_RE.search(body)
        category_match = CATEGORY_RE.search(body)
        items.append(
            SearchResultItem(
                tid=tid,
                title=title,
                publisher=None if publisher_match is None else _strip_html(publisher_match.group("publisher")),
                posted_at=None if date_match is None else date_match.group("date"),
                category=None if category_match is None else _strip_html(category_match.group("category")),
                reply_count=None if meta_match is None else int(meta_match.group("reply")),
                excerpt=None if excerpt_match is None else _strip_html(excerpt_match.group("excerpt")),
                url=href,
            )
        )
    return items
