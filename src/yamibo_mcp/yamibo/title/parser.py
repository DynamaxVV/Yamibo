from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field

from yamibo_mcp.yamibo.title.normalizer import normalize_display_title, normalize_series_key, strip_discuz_suffix


@dataclass(frozen=True)
class TitleParseResult:
    display_title: str
    group_name: str | None
    author_guess: str | None
    core_title_guess: str
    normalized_core_title: str
    series_key: str
    title_aliases: list[str] = field(default_factory=list)
    chapter_name: str | None = None
    chapter_index: float | None = None
    chapter_index_end: float | None = None
    chapter_title: str | None = None
    subtitle: str | None = None
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.8
    needs_review: bool = False


_PREFIX_RE = re.compile(r"^\s*(?:【([^】]+)】|\[([^\]]+)\]|\((C\d+)\))")
_AUTHOR_RE = re.compile(r"^\s*(?:\[([^\]]+)\]|［([^］]+)］)")
_SPECIAL_KEYWORDS = frozenset(("特典", "彩页", "番外", "加笔", "贴附", "上篇", "中篇", "下篇", "前篇", "后篇", "後篇"))
_TAG_KEYWORDS = frozenset(("童", "GBC", "!"))
_CHAPTER_RE = re.compile(
    r"\s+(?P<chapter>"
    r"(?P<range_start>\d+)~(?P<range_end>\d+)|"
    r"(?P<colon_number>\d+)\s*[:：]\s*(?P<colon_title>\S+)|"
    r"第\s*(?P<number>\d+(?:[.\-－]\d+)?)\s*[话話回章](?:[（(][^)）]+[)）])?"
    r"(?P<suffix>前篇|后篇|後篇|其\d+|[上中下前后後])?|"
    r"(?P<bare_number>\d+(?:[.\-－]\d+)?)\s*[话話回章]"
    r"(?P<suffix2>前篇|后篇|後篇|其\d+|[上中下前后後])?|"
    r"(?P<special>" + "|".join(_SPECIAL_KEYWORDS) + r")"
    r")"
    r"(?:\s*[-—·]\s*|\s+)?(?P<chapter_title>.+)?\s*$"
)
_SUBTITLE_RE = re.compile(r"(.+?)\s*[「『](.+?)[」』]\s*$")


def _pop_prefixes(value: str) -> tuple[str | None, list[str], str]:
    group_name: str | None = None
    tags: list[str] = []
    rest = value
    while True:
        match = _PREFIX_RE.match(rest)
        if not match:
            break
        token = next(part for part in match.groups() if part)
        is_square_bracket = match.group(1) is not None
        is_group = any(word in token for word in ("组", "組", "汉化", "漢化", "转载", "轉載", "研究中心"))
        is_tag = token in _TAG_KEYWORDS or bool(re.fullmatch(r"C\d+", token))
        if group_name is None and is_group:
            group_name = token
        elif is_tag:
            tags.append(token)
        elif is_square_bracket:
            rest = rest[match.end():].strip()
            continue
        else:
            break
        rest = rest[match.end():].strip()
    return group_name, tags, rest


def _pop_author(value: str) -> tuple[str | None, str]:
    match = _AUTHOR_RE.match(value)
    if not match:
        return None, value
    author = match.group(1) or match.group(2)
    return author, value[match.end() :].strip()


def _pop_chapter(value: str) -> tuple[str, str | None, float | None, float | None, str | None]:
    match = _CHAPTER_RE.search(value)
    if not match:
        return value, None, None, None, None
    chapter_name = normalize_display_title(match.group("chapter"))
    range_start = match.group("range_start")
    range_end = match.group("range_end")
    colon_number = match.group("colon_number")
    colon_title = match.group("colon_title")
    special = match.group("special")
    chapter_index = None
    chapter_index_end = None
    chapter_title = None
    if range_start and range_end:
        try:
            chapter_index = float(range_start)
            chapter_index_end = float(range_end)
        except ValueError:
            pass
    elif colon_number is not None:
        try:
            chapter_index = float(colon_number)
        except ValueError:
            pass
        chapter_name = colon_number
        chapter_title = normalize_display_title(colon_title) if colon_title else None
    elif special:
        chapter_index = 999.0
        chapter_name = f"999 {special}"
    else:
        raw_number = match.group("number") or match.group("bare_number")
        if raw_number:
            try:
                chapter_index = float(raw_number.replace("－", "-").replace("-", "."))
            except ValueError:
                chapter_index = None
            suffix = match.group("suffix") or match.group("suffix2")
            if suffix and chapter_index is not None:
                if suffix in ("上", "前", "前篇"):
                    chapter_index += 0.1
                elif suffix in ("下", "後", "后", "后篇", "後篇"):
                    chapter_index += 0.2
                elif suffix.startswith("其"):
                    try:
                        chapter_index += int(suffix[1:]) / 10
                    except ValueError:
                        pass
    if chapter_title is None:
        raw_chapter_title = match.group("chapter_title") or ""
        chapter_title = normalize_display_title(raw_chapter_title)
    return value[: match.start()].strip(), chapter_name, chapter_index, chapter_index_end, (chapter_title or None)


def _split_aliases(value: str) -> tuple[str, list[str]]:
    for sep in ("/", "|", "｜"):
        if sep in value:
            # 双语标题先保留一个主标题，其余部分记成 alias，避免直接拆成多个系列。
            parts = [part.strip() for part in value.split(sep) if part.strip()]
            if len(parts) >= 2:
                return parts[0], parts[1:]
    return value, []


def parse_title(raw_title: str) -> TitleParseResult:
    display_title = strip_discuz_suffix(html_lib.unescape(raw_title))
    group_name, tags, rest = _pop_prefixes(display_title)
    author_guess, rest = _pop_author(rest)
    subtitle = None
    subtitle_match = _SUBTITLE_RE.match(rest)
    if subtitle_match:
        rest = subtitle_match.group(1).strip()
        subtitle = subtitle_match.group(2).strip()
    rest, chapter_name, chapter_index, chapter_index_end, chapter_title = _pop_chapter(rest)
    if chapter_name is None and chapter_index is not None:
        chapter_name = str(int(chapter_index))
    if chapter_name is None and chapter_index is None:
        chapter_name = "1"
        chapter_index = 1.0
    core_title, aliases = _split_aliases(rest)
    core_title = normalize_display_title(core_title)
    normalized_core_title = normalize_display_title(core_title)
    series_key = normalize_series_key(core_title)
    confidence = 0.9 if core_title and series_key else 0.4
    needs_review = confidence < 0.75 or len(core_title) <= 3 or bool(aliases)
    return TitleParseResult(
        display_title=display_title,
        group_name=group_name,
        author_guess=author_guess,
        core_title_guess=core_title,
        normalized_core_title=normalized_core_title,
        series_key=series_key,
        title_aliases=aliases,
        chapter_name=chapter_name,
        chapter_index=chapter_index,
        chapter_index_end=chapter_index_end,
        chapter_title=chapter_title,
        subtitle=subtitle,
        tags=tags,
        confidence=confidence,
        needs_review=needs_review,
    )
