from __future__ import annotations

import re
from html import escape as html_escape
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin


_FONT_SIZE_SCALE = {
    1: "0.75em",
    2: "0.875em",
    3: "1em",
    4: "1.125em",
    5: "1.375em",
    6: "1.75em",
    7: "2.25em",
}

_SAFE_STYLE_KEYS = {"color", "font-weight", "font-style", "text-decoration", "font-size", "text-align"}


def _parse_style_map(style_value: str | None) -> dict[str, str]:
    if not style_value:
        return {}
    result: dict[str, str] = {}
    for part in style_value.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key or not value or key not in _SAFE_STYLE_KEYS:
            continue
        if key == "color":
            color = _sanitize_color(value)
            if color:
                result[key] = color
            continue
        if key == "font-weight":
            weight = _sanitize_font_weight(value)
            if weight:
                if weight in {"normal", "400"}:
                    continue
                result[key] = weight
            continue
        if key == "font-style":
            style = value.lower()
            if style in {"italic", "oblique"}:
                result[key] = style
            continue
        if key == "text-decoration":
            decoration = _sanitize_text_decoration(value)
            if decoration and decoration != "none":
                result[key] = decoration
            continue
        if key == "text-align":
            align = value.lower()
            if align in {"left", "center", "right", "justify"}:
                result[key] = align
            continue
        if key == "font-size":
            size = _sanitize_font_size(value)
            if size and size not in {"1em", "16px"}:
                result[key] = size
    if result.get("color") in {"#000000", "black"}:
        result.pop("color", None)
    return result


def _sanitize_color(value: str) -> str | None:
    value = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value):
        return value
    if re.fullmatch(r"[a-zA-Z]+", value):
        return value.lower()
    if re.fullmatch(r"rgba?\([0-9.,\s%]+\)", value):
        return value
    return None


def _sanitize_font_weight(value: str) -> str | None:
    value = value.strip().lower()
    if value in {"normal", "bold", "bolder", "lighter"}:
        return "700" if value == "bold" else value
    if value.isdigit():
        return value
    return None


def _sanitize_text_decoration(value: str) -> str | None:
    parts = [part for part in re.split(r"\s+", value.strip().lower()) if part]
    if not parts:
        return None
    allowed = [part for part in parts if part in {"underline", "line-through", "overline", "none"}]
    if not allowed:
        return None
    if "none" in allowed:
        return "none"
    return " ".join(dict.fromkeys(allowed))


def _sanitize_font_size(value: str) -> str | None:
    value = value.strip().lower()
    if re.fullmatch(r"\d+(?:\.\d+)?(px|em|rem|pt|%)", value):
        return value
    if value.isdigit():
        return _FONT_SIZE_SCALE.get(int(value))
    return None


def sanitize_href(value: str, *, base_url: str | None = None) -> str | None:
    href = value.strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    lower = href.lower()
    if lower.startswith(("javascript:", "data:", "vbscript:")):
        return None
    try:
        parsed = urlparse(href)
    except ValueError:
        return None
    if not parsed.scheme and base_url:
        try:
            href = urljoin(base_url, href)
            parsed = urlparse(href)
        except ValueError:
            return None
    if parsed.scheme and parsed.scheme not in {"http", "https", "mailto", "tel"}:
        return None
    return href


def format_style(style_map: dict[str, str]) -> str | None:
    if not style_map:
        return None
    return ";".join(f"{key}:{value}" for key, value in style_map.items())


_RICH_BLOCK_TAGS = {"div", "p", "blockquote", "ul", "ol", "li", "pre"}
_RICH_IGNORED_TAGS: set[str] = set()


class _RichBodyNormalizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._top_level_parts: list[str] = []
        self._block_depth = 0
        self._tag_stack: list[tuple[str, bool]] = []

    def result(self) -> str:
        self._flush_top_level()
        return "".join(self._parts).strip()

    def handle_starttag(self, tag: str, attrs):
        if tag == "br":
            if self._block_depth == 0:
                self._flush_top_level()
            else:
                self._parts.append("<br>")
            self._tag_stack.append((tag, True))
            return
        if tag in _RICH_IGNORED_TAGS:
            self._tag_stack.append((tag, True))
            return
        rendered = self._render_starttag(tag, attrs)
        dropped = rendered == ""
        self._tag_stack.append((tag, dropped))
        if tag in _RICH_BLOCK_TAGS:
            if self._block_depth == 0:
                self._flush_top_level()
            if not dropped:
                self._parts.append(rendered)
            self._block_depth += 1
            return
        if self._block_depth == 0:
            if not dropped:
                self._top_level_parts.append(rendered)
            return
        if not dropped:
            self._parts.append(rendered)

    def handle_endtag(self, tag: str):
        if tag in _RICH_IGNORED_TAGS:
            self._pop_tag(tag)
            return
        dropped = self._pop_tag(tag)
        rendered = f"</{tag}>"
        if tag in _RICH_BLOCK_TAGS:
            if self._block_depth > 0:
                self._block_depth -= 1
            if not dropped:
                self._parts.append(rendered)
            return
        if self._block_depth == 0:
            if not dropped:
                self._top_level_parts.append(rendered)
            return
        if not dropped:
            self._parts.append(rendered)

    def handle_data(self, data: str):
        if not data:
            return
        escaped = html_escape(data)
        if self._block_depth == 0:
            if data.strip():
                self._top_level_parts.append(escaped)
            return
        self._parts.append(escaped)

    def _flush_top_level(self):
        text = "".join(self._top_level_parts).strip()
        if text:
            self._parts.append(f"<p>{text}</p>")
        self._top_level_parts = []

    def _pop_tag(self, tag: str) -> bool:
        for index in range(len(self._tag_stack) - 1, -1, -1):
            stack_tag, dropped = self._tag_stack[index]
            del self._tag_stack[index]
            if stack_tag == tag:
                return dropped
        return False

    def _render_starttag(self, tag: str, attrs) -> str:
        if tag == "a":
            href = None
            for key, value in attrs:
                if key.lower() == "href" and value is not None:
                    href = sanitize_href(value)
                    break
            if not href:
                return ""
            return f'<a href="{html_escape(href, quote=True)}" target="_blank" rel="noreferrer">'
        rendered_attrs: list[str] = []
        for key, value in attrs:
            if value is None:
                continue
            if key.lower() == "style":
                style_map = _parse_style_map(value)
                style = format_style(style_map)
                if style:
                    rendered_attrs.append(f'style="{html_escape(style, quote=True)}"')
                continue
            rendered_attrs.append(f'{key}="{html_escape(value, quote=True)}"')
        if tag == "span" and not rendered_attrs:
            return ""
        if not rendered_attrs:
            return f"<{tag}>"
        return f"<{tag}{(' ' + ' '.join(rendered_attrs)) if rendered_attrs else ''}>"


def normalize_rich_body_html(html: str | None) -> str | None:
    if not html:
        return None
    parser = _RichBodyNormalizer()
    parser.feed(html)
    return parser.result() or None
