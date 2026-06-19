from __future__ import annotations

import re
from html.parser import HTMLParser


def attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key: value or "" for key, value in attrs}


def extract_tid(value: str) -> int | None:
    patterns = [
        r"thread-(\d+)-",
        r"[?&]tid=(\d+)",
        r"id=['\"]?(\d+)['\"]?",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return int(match.group(1))
    return None


class TextCaptureParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []

    @property
    def path(self) -> str:
        return "/".join(self._stack)

    def handle_starttag(self, tag: str, attrs):
        self._stack.append(tag)

    def handle_endtag(self, tag: str):
        if self._stack:
            self._stack.pop()
