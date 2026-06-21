from __future__ import annotations

from html import escape as html_escape
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import urljoin

from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.yamibo.cleaners.content_cleaner import clean_content
from yamibo_mcp.yamibo.page_classifier import PageType, classify_html
from yamibo_mcp.yamibo.parsers.common import TextCaptureParser, attrs_dict
from yamibo_mcp.yamibo.title.parser import parse_title
from yamibo_mcp.yamibo.title.normalizer import normalize_display_title


@dataclass(frozen=True)
class ThreadDetailSummary:
    title: str | None
    floors: list[FloorSnapshot]


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
                result[key] = weight
            continue
        if key == "font-style":
            style = value.lower()
            if style in {"italic", "oblique", "normal"}:
                result[key] = style
            continue
        if key == "text-decoration":
            decoration = _sanitize_text_decoration(value)
            if decoration:
                result[key] = decoration
            continue
        if key == "text-align":
            align = value.lower()
            if align in {"left", "center", "right", "justify"}:
                result[key] = align
            continue
        if key == "font-size":
            size = _sanitize_font_size(value)
            if size:
                result[key] = size
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


def _sanitize_href(value: str, *, base_url: str | None = None) -> str | None:
    href = value.strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    lower = href.lower()
    if lower.startswith(("javascript:", "data:", "vbscript:")):
        return None
    parsed = urlparse(href)
    if not parsed.scheme and base_url:
        href = urljoin(base_url, href)
        parsed = urlparse(href)
    if parsed.scheme and parsed.scheme not in {"http", "https", "mailto", "tel"}:
        return None
    return href


def _format_style(style_map: dict[str, str]) -> str | None:
    if not style_map:
        return None
    return ";".join(f"{key}:{value}" for key, value in style_map.items())


class _ThreadSubjectParser(TextCaptureParser):
    _BLOCK_BREAK_START_TAGS = {"br"}
    _BLOCK_BREAK_END_TAGS = {"div", "p", "li", "tr", "blockquote"}

    def __init__(self, *, base_url: str | None = None):
        super().__init__()
        self._base_url = base_url
        self._capture_subject = False
        self._subject_parts: list[str] = []
        self._capture_floor = False
        self._floor_td_depth = 0
        self._floor_pid: int | None = None
        self._floor_parts: list[str] = []
        self._rich_parts: list[str] = []
        self._rich_tag_stack: list[tuple[str, str | None]] = []
        self._floor_no = 0
        self._floors: list[FloorSnapshot] = []
        self._floor_has_images = False
        self._floor_image_urls: list[str] = []
        self._in_quote = False
        self._quote_parts: list[str] = []
        self._reply_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        data = attrs_dict(attrs)
        if tag == "span" and data.get("id") == "thread_subject":
            self._capture_subject = True
        if tag == "td" and data.get("id", "").startswith("postmessage_"):
            try:
                self._floor_pid = int(data["id"].split("_", 1)[1])
            except ValueError:
                self._floor_pid = None
            self._capture_floor = True
            self._floor_td_depth = 1
            self._floor_parts = []
            self._rich_parts = []
            self._rich_tag_stack = []
            self._floor_has_images = False
            self._floor_image_urls = []
            self._in_quote = False
            self._quote_parts = []
            self._reply_parts = []
            self._skip_depth = 0
        elif self._capture_floor and tag == "td":
            self._floor_td_depth += 1
        if self._capture_floor and tag == "img":
            image_url = self._resolve_image_url(data)
            if image_url:
                self._floor_image_urls.append(image_url)
            self._floor_has_images = self._floor_has_images or bool(image_url) or True
        if self._capture_floor and tag == "div" and "quote" in set(data.get("class", "").split()):
            self._in_quote = True
            self._quote_parts = []
        if self._capture_floor:
            if self._skip_depth > 0:
                self._skip_depth += 1
                super().handle_starttag(tag, attrs)
                return
            if self._should_skip_tag(tag, data):
                self._skip_depth = 1
                super().handle_starttag(tag, attrs)
                return
            rich_open, rich_close = self._render_rich_start(tag, data)
            if rich_open is not None:
                self._rich_parts.append(rich_open)
                if rich_close is not None:
                    self._rich_tag_stack.append((tag, rich_close))
        super().handle_starttag(tag, attrs)
        if self._capture_floor and tag in self._BLOCK_BREAK_START_TAGS:
            self._append_floor_break()

    def handle_endtag(self, tag: str):
        if tag == "span" and self._capture_subject:
            self._capture_subject = False
        if tag == "div" and self._in_quote:
            self._in_quote = False
        if self._capture_floor and self._skip_depth > 0:
            self._skip_depth -= 1
            super().handle_endtag(tag)
            return
        if self._capture_floor and tag in self._BLOCK_BREAK_END_TAGS:
            self._append_floor_break()
        if self._capture_floor:
            for index in range(len(self._rich_tag_stack) - 1, -1, -1):
                source_tag, close_tag = self._rich_tag_stack[index]
                if source_tag != tag:
                    continue
                self._rich_parts.append(close_tag or "")
                del self._rich_tag_stack[index]
                break
        if tag == "td" and self._capture_floor:
            self._floor_td_depth -= 1
            if self._floor_td_depth <= 0:
                if self._floor_pid is not None:
                    self._floor_no += 1
                    content = clean_content("".join(self._floor_parts))
                    quote_text = clean_content("".join(self._quote_parts)) if self._quote_parts else None
                    reply_text = clean_content("".join(self._reply_parts)) if self._reply_parts else None
                    while self._rich_tag_stack:
                        _, close_tag = self._rich_tag_stack.pop()
                        if close_tag:
                            self._rich_parts.append(close_tag)
                    rich_body_html = "".join(self._rich_parts).strip() or None
                    self._floors.append(
                        FloorSnapshot(
                            pid=self._floor_pid,
                            tid=0,
                            floor_no=self._floor_no,
                            publisher=None,
                            content=content,
                            pub_time=None,
                            has_images=self._floor_has_images,
                            publisher_uid=None,
                            image_urls=self._floor_image_urls.copy(),
                            quote_text=quote_text,
                            reply_text=reply_text,
                            rich_body_html=rich_body_html,
                        )
                    )
                self._capture_floor = False
                self._floor_td_depth = 0
                self._floor_pid = None
        super().handle_endtag(tag)

    def handle_data(self, data: str):
        if self._capture_subject:
            self._subject_parts.append(data)
        if self._capture_floor:
            if self._skip_depth > 0:
                return
            if data:
                self._rich_parts.append(html_escape(data))
            if not data.strip():
                return
            self._floor_parts.append(data)
            if self._in_quote:
                self._quote_parts.append(data)
            else:
                self._reply_parts.append(data)

    def _append_floor_break(self):
        if not self._capture_floor:
            return
        if self._floor_parts and not self._floor_parts[-1].endswith("\n"):
            self._floor_parts.append("\n")
        if self._in_quote:
            if self._quote_parts and not self._quote_parts[-1].endswith("\n"):
                self._quote_parts.append("\n")
        else:
            if self._reply_parts and not self._reply_parts[-1].endswith("\n"):
                self._reply_parts.append("\n")

    def result(self) -> ThreadDetailSummary:
        title = normalize_display_title("".join(self._subject_parts)) if self._subject_parts else None
        return ThreadDetailSummary(title=title, floors=self._floors)

    def _should_skip_tag(self, tag: str, data: dict[str, str]) -> bool:
        class_name = data.get("class", "")
        classes = set(class_name.split())
        return tag == "ignore_js_op" or "ignore_js_op" in classes or (tag == "i" and "pstatus" in classes)

    def _render_rich_start(self, tag: str, data: dict[str, str]) -> tuple[str | None, str | None]:
        classes = set(data.get("class", "").split())
        if "quote" in classes and tag in {"div", "blockquote"}:
            return "<blockquote>", "</blockquote>"
        if tag in {"html", "body", "tbody", "thead", "tfoot", "tr", "td", "th"}:
            return None, None
        if tag == "br":
            return "<br>", None
        if tag in {"strong", "b"}:
            return "<strong>", "</strong>"
        if tag in {"em", "i"}:
            return "<em>", "</em>"
        if tag == "u":
            return "<u>", "</u>"
        if tag in {"s", "strike", "del"}:
            return "<s>", "</s>"
        if tag == "blockquote":
            return "<blockquote>", "</blockquote>"
        if tag == "a":
            href = _sanitize_href(data.get("href", ""), base_url=self._base_url)
            if href:
                return f'<a href="{html_escape(href, quote=True)}">', "</a>"
            return "<a>", "</a>"
        if tag == "font":
            style_map: dict[str, str] = {}
            color = _sanitize_color(data.get("color", ""))
            if color:
                style_map["color"] = color
            size = _sanitize_font_size(data.get("size", ""))
            if size:
                style_map["font-size"] = size
            inline_style = _parse_style_map(data.get("style"))
            style_map.update(inline_style)
            if style_map:
                style = _format_style(style_map)
                return f'<span style="{html_escape(style, quote=True)}">', "</span>"
            return "<span>", "</span>"
        if tag == "span":
            style_map = _parse_style_map(data.get("style"))
            if style_map:
                style = _format_style(style_map)
                return f'<span style="{html_escape(style, quote=True)}">', "</span>"
            return None, None
        if tag == "div":
            style_map = _parse_style_map(data.get("style"))
            align = data.get("align", "").strip().lower()
            if align in {"left", "center", "right", "justify"}:
                style_map.setdefault("text-align", align)
            if style_map:
                style = _format_style(style_map)
                return f'<div style="{html_escape(style, quote=True)}">', "</div>"
            return "<div>", "</div>"
        if tag == "p":
            style_map = _parse_style_map(data.get("style"))
            if style_map:
                style = _format_style(style_map)
                return f'<p style="{html_escape(style, quote=True)}">', "</p>"
            return "<p>", "</p>"
        if tag == "center":
            return '<div style="text-align:center">', "</div>"
        if tag == "li":
            return "<li>", "</li>"
        if tag == "ol":
            return "<ol>", "</ol>"
        if tag == "ul":
            return "<ul>", "</ul>"
        return None, None

    def _resolve_image_url(self, attrs: dict[str, str]) -> str | None:
        return _resolve_image_url_from_attrs(attrs, base_url=self._base_url)


def parse_thread_detail(html: str, *, base_url: str | None = None) -> ThreadDetailSummary:
    parser = _ThreadSubjectParser(base_url=base_url)
    parser.feed(html)
    summary = parser.result()
    floors: list[FloorSnapshot] = []
    for floor in summary.floors:
        block_image_urls = _extract_floor_image_urls_from_post_block(html, pid=floor.pid, base_url=base_url)
        if block_image_urls:
            deduped_urls: list[str] = []
            seen: set[str] = set()
            for image_url in block_image_urls:
                if image_url in seen:
                    continue
                seen.add(image_url)
                deduped_urls.append(image_url)
            floors.append(
                FloorSnapshot(
                    pid=floor.pid,
                    tid=floor.tid,
                    floor_no=floor.floor_no,
                    publisher=floor.publisher,
                    content=floor.content,
                    pub_time=floor.pub_time,
                    has_images=bool(deduped_urls),
                    publisher_uid=None,
                    image_urls=deduped_urls,
                    quote_text=floor.quote_text,
                    reply_text=floor.reply_text,
                    rich_body_html=floor.rich_body_html,
                )
            )
        else:
            floors.append(floor)
    return ThreadDetailSummary(title=summary.title, floors=floors)


def parse_thread_snapshot(html: str, *, url: str | None = None, tid: int | None = None) -> ThreadSnapshot:
    classification = classify_html(html)
    # 解析快照前先做页面级护栏，非详情页直接拒绝进入归档链路。
    if classification.page_type != PageType.THREAD_DETAIL:
        raise ValueError(f"expected thread_detail page, got {classification.page_type.value}")
    summary = parse_thread_detail(html, base_url=url)
    attachment_download_map = _extract_attachment_download_map(html, base_url=url)
    raw_title = summary.title or ""
    parsed_title = parse_title(raw_title)
    resolved_tid = tid or _extract_tid_from_html_or_url(html, url)
    post_meta = _extract_post_meta(html)
    floors = [
        FloorSnapshot(
            pid=floor.pid,
            tid=resolved_tid,
            floor_no=floor.floor_no,
            publisher=post_meta.get(floor.pid, {}).get("publisher"),
            content=floor.content,
            pub_time=post_meta.get(floor.pid, {}).get("pub_time"),
            has_images=floor.has_images,
            publisher_uid=post_meta.get(floor.pid, {}).get("publisher_uid"),
            image_urls=[attachment_download_map.get(image_url, image_url) for image_url in floor.image_urls],
            quote_text=floor.quote_text,
            reply_text=floor.reply_text,
            rich_body_html=floor.rich_body_html,
        )
        for floor in summary.floors
    ]
    first_post_meta = post_meta.get(floors[0].pid, {}) if floors else {}
    return ThreadSnapshot(
        tid=resolved_tid,
        url=url,
        page_type=classification.page_type.value,
        raw_title=raw_title,
        display_title=raw_title,
        title=TitleSnapshot(
            raw_title=raw_title,
            display_title=parsed_title.display_title,
            group_name=parsed_title.group_name,
            author_guess=parsed_title.author_guess,
            core_title_guess=parsed_title.core_title_guess,
            normalized_core_title=parsed_title.normalized_core_title,
            series_key=parsed_title.series_key,
            title_aliases=parsed_title.title_aliases,
            chapter_name=parsed_title.chapter_name,
            chapter_index=parsed_title.chapter_index,
            chapter_index_end=parsed_title.chapter_index_end,
            chapter_title=parsed_title.chapter_title,
            subtitle=parsed_title.subtitle,
            tags=parsed_title.tags,
            confidence=parsed_title.confidence,
            needs_review=parsed_title.needs_review,
        ),
        publisher=first_post_meta.get("publisher"),
        publisher_uid=first_post_meta.get("publisher_uid"),
        pub_time=first_post_meta.get("pub_time"),
        permission=0,
        floors=floors,
        image_count=sum(len(floor.image_urls) if floor.image_urls else int(floor.has_images) for floor in floors),
    )


def _extract_tid_from_html_or_url(html: str, url: str | None) -> int:
    candidates = []
    if url:
        candidates.append(url)
    candidates.append(html[:1000])
    for candidate in candidates:
        match = re.search(r"thread-(\d+)-", candidate) or re.search(r"tid=(\d+)", candidate)
        if match:
            return int(match.group(1))
    raise ValueError("Unable to resolve tid")


_FORUM_LINK_RE = re.compile(r'forum-(?P<fid>\d+)-\d+\.html')
_FORUM_PHP_RE = re.compile(r'forum\.php\?mod=forumdisplay[^>]*fid=(\d+)')
_TYPEID_RE = re.compile(r'forum\.php\?mod=forumdisplay[^>]*fid=(\d+)[^>]*filter=typeid[^>]*typeid=(\d+)[^>]*>([^<]*)</a>')
_CATEGORY_RE = re.compile(r'\[<a [^>]*filter=typeid[^>]*>(?P<category>.*?)</a>\]', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

_FID_FORUM_NAMES: dict[int, str] = {
    5: "動漫區", 13: "貼圖區", 16: "管理版", 19: "資源交流區",
    30: "中文百合漫画区", 33: "海域區", 44: "遊戲區", 49: "文學區",
    55: "轻小说/译文区", 370: "使用指南", 379: "影視區",
}

_FID_TYPEID_NAMES: dict[int, dict[int, str]] = {
    33: {
        4: "公告", 399: "活动", 400: "动画讨论", 401: "漫画讨论",
        515: "轻小说", 516: "音声", 405: "2.5次元", 404: "情报",
        303: "翻译资料", 402: "推荐", 403: "求推", 1: "杂谈",
        256: "争议慎跳", 257: "别拦着我", 3: "其他",
        408: "八卦杂谈", 409: "情感树洞", 406: "为什么呀",
        410: "理性探讨", 407: "新闻资讯", 272: "电脑数码",
        273: "宰客留情", 326: "互相安利", 412: "呼朋引伴",
        276: "女王教室", 270: "影音殿堂", 271: "偶像团体",
        328: "姬情安价", 411: "水库科研", 274: "Cosplay",
        275: "百合wiki", 325: "活动", 268: "公告", 269: "其它文",
    },
    30: {
        65: "公告", 398: "韩国漫画", 503: "泰国漫画", 504: "欧美其他",
        69: "長篇連載", 68: "短篇漫畫", 66: "百合雜誌", 70: "奇字標記",
        71: "單行本", 234: "東方", 225: "聖母在上", 335: "光之美少女",
        224: "HIME", 227: "魔炮", 229: "SW", 236: "K-ON!",
        228: "SAKI", 230: "魔法少女小圓", 343: "戦姫絶唱",
        341: "少女与战车", 231: "LoveLive!", 338: "佐贺偶像是传奇",
        226: "進擊的巨人", 340: "RWBY", 232: "艦これ",
        342: "響け!", 329: "BanG Dream", 336: "赛马娘",
        333: "少女☆歌剧", 330: "偶像大師", 337: "偶像活动",
        339: "摇曳露营", 477: "孤独摇滚", 479: "Lycoris Recoil",
        470: "水星的魔女", 478: "绯染天空", 480: "Assault Lily",
        421: "Vtuber",
    },
    55: {
        147: "公告", 295: "轻小说", 235: "其他小说",
        149: "文学/文艺小说", 146: "其它",
    },
}


def _extract_forum_id_from_links(html: str) -> int | None:
    matches = _FORUM_LINK_RE.findall(html)
    if not matches:
        return None
    from yamibo_mcp.domain.forums import resolve_forum
    recognized: list[int] = []
    for fid_str in matches:
        fid = int(fid_str)
        profile = resolve_forum(fid)
        if profile.content_kind != "unknown":
            recognized.append(fid)
    if recognized:
        return recognized[-1]
    return int(matches[-1])


def extract_forum_id_from_html(html: str) -> int | None:
    for m in _TYPEID_RE.finditer(html):
        fid = int(m.group(1))
        if fid in _FID_FORUM_NAMES:
            return fid
    for m in _FORUM_PHP_RE.finditer(html):
        fid = int(m.group(1))
        if fid in _FID_FORUM_NAMES:
            return fid
    subject_marker = 'id="thread_subject"'
    if subject_marker in html:
        # 面包屑导航通常位于帖子标题前面，优先在标题之前的区域取最后一个已知版块。
        breadcrumb_html = html[:html.find(subject_marker)]
        forum_id = _extract_forum_id_from_links(breadcrumb_html)
        if forum_id is not None:
            return forum_id
    forum_id = _extract_forum_id_from_links(html)
    if forum_id is not None:
        return forum_id
    return None


def extract_category_from_html(html: str) -> str | None:
    for m in _TYPEID_RE.finditer(html):
        fid = int(m.group(1))
        typeid = int(m.group(2))
        type_map = _FID_TYPEID_NAMES.get(fid, {})
        if typeid in type_map:
            return type_map[typeid]
        return _TAG_RE.sub("", m.group(3)).strip() or None
    match = _CATEGORY_RE.search(html)
    if match:
        return _TAG_RE.sub("", match.group("category")).strip() or None
    return None


def extract_author_only_total_pages(html: str, *, tid: int, author_uid: str) -> int | None:
    href_pattern = re.compile(
        rf'href="[^"]*mod=viewthread[^"]*tid={tid}[^"]*authorid={re.escape(str(author_uid))}[^"]*page=(\d+)[^"]*"',
        re.IGNORECASE,
    )
    pages = [int(value) for value in href_pattern.findall(html)]
    span_match = re.search(r'<span title="共 (\d+) 页">', html)
    if span_match is not None:
        pages.append(int(span_match.group(1)))
    return max(pages) if pages else None


def _extract_post_meta(html: str) -> dict[int, dict[str, str | None]]:
    meta: dict[int, dict[str, str | None]] = {}
    block_pattern = re.compile(r'<div id="post_(?P<pid>\d+)"[^>]*>(?P<body>.*?)(?=<div id="post_\d+"|$)', re.S)
    publisher_pattern = re.compile(
        r'<div class="authi"><a href="[^"]*space-uid-(?P<uid>\d+)\.html"[^>]*>(?P<publisher>.*?)</a>',
        re.S,
    )
    pub_time_pattern = re.compile(r'<em id="authorposton(?P<pid>\d+)">发表于 (?P<pub_time>\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})</em>')
    for match in block_pattern.finditer(html):
        pid = int(match.group("pid"))
        body = match.group("body")
        publisher_match = publisher_pattern.search(body)
        pub_time_match = pub_time_pattern.search(body)
        if not publisher_match and not pub_time_match:
            continue
        meta[pid] = {
            "publisher": None if publisher_match is None else normalize_display_title(publisher_match.group("publisher")),
            "publisher_uid": None if publisher_match is None else publisher_match.group("uid"),
            "pub_time": None if pub_time_match is None else pub_time_match.group("pub_time"),
        }
    return meta


def _extract_attachment_download_map(html: str, *, base_url: str | None) -> dict[str, str]:
    if not base_url:
        return {}
    aid_to_file: dict[str, str] = {}
    for tag in re.findall(r"<img\b[^>]*>", html, flags=re.IGNORECASE):
        aid_match = re.search(r'\baid="(?P<aid>\d+)"', tag)
        file_match = re.search(r'\b(?:zoomfile|file)="(?P<file>[^"]+)"', tag)
        if aid_match is None or file_match is None:
            continue
        aid_to_file[aid_match.group("aid")] = urljoin(base_url, file_match.group("file"))

    result: dict[str, str] = {}
    tip_pattern = re.compile(
        r'<div[^>]+id="aimg_(?P<aid>\d+)_menu"[^>]*>.*?<a href="(?P<href>[^"]*forum\.php\?mod=attachment[^"]*nothumb=yes[^"]*)"',
        re.S | re.IGNORECASE,
    )
    for match in tip_pattern.finditer(html):
        aid = match.group("aid")
        source_url = aid_to_file.get(aid)
        if not source_url:
            continue
        result[source_url] = urljoin(base_url, match.group("href").replace("&amp;", "&"))
    return result


def _extract_floor_image_urls_from_post_block(html: str, *, pid: int, base_url: str | None) -> list[str]:
    start_marker = f'id="postmessage_{pid}"'
    start = html.find(start_marker)
    if start < 0:
        return []
    end_marker = f'id="comment_{pid}"'
    end = html.find(end_marker, start)
    if end < 0:
        next_post = re.search(r'<div id="post_\d+"', html[start:], re.S)
        end = start + next_post.start() if next_post and next_post.start() > 0 else len(html)
    block = html[start:end]
    image_urls: list[str] = []
    for tag in re.findall(r"<img\b[^>]*>", block, flags=re.IGNORECASE):
        image_url = _resolve_image_url_from_tag(tag, base_url=base_url)
        if image_url:
            image_urls.append(image_url)
    return image_urls


def _resolve_image_url_from_tag(tag: str, *, base_url: str | None) -> str | None:
    attrs = dict(re.findall(r'([a-zA-Z0-9_:-]+)="([^"]*)"', tag))
    return _resolve_image_url_from_attrs(attrs, base_url=base_url)


def _resolve_image_url_from_attrs(attrs: dict[str, str], *, base_url: str | None) -> str | None:
    preferred_local = _resolve_local_saved_image(attrs, base_url=base_url)
    if preferred_local is not None:
        return preferred_local
    raw = attrs.get("file") or attrs.get("zoomfile") or attrs.get("src") or attrs.get("data-src") or ""
    raw = raw.strip()
    if not raw or raw.lstrip("/").startswith(("data:", "javascript:")):
        return None
    return urljoin(base_url, raw) if base_url else raw


def _resolve_local_saved_image(attrs: dict[str, str], *, base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    if parsed.scheme != "file":
        return None
    src = (attrs.get("src") or attrs.get("data-src") or "").strip()
    if not src or src.startswith(("data:", "javascript:")):
        return None
    resolved = urljoin(base_url, src)
    resolved_parsed = urlparse(resolved)
    if resolved_parsed.scheme != "file":
        return None
    local_path = Path(resolved_parsed.path)
    return resolved if local_path.exists() else None
