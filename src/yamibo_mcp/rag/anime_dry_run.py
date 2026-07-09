from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.rag.chunker import split_text_for_embedding
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.yamibo.cleaners.content_cleaner import clean_content


EvidenceLane = Literal[
    "discussion_evidence",
    "background_material",
    "source_text",
    "low_signal",
    "unknown",
]

ANIME_FORUM_ID = 5
DRY_RUN_VERSION = "anime-evidence-lane-dry-run-v1"
VALID_ARCHIVE_STATUSES = ("complete", "partial")
_VALID_ARCHIVE_STATUS_SQL = "('complete', 'partial')"

_DISCUSSION_TERMS = (
    "讨论",
    "觀後感",
    "观后感",
    "感想",
    "吐槽",
    "杂谈",
    "雜談",
    "八卦",
    "求推",
    "推荐",
    "推薦",
    "怎么看",
    "怎麼看",
    "聊聊",
    "投票",
    "争议",
    "爭議",
    "党争",
    "cp",
    "百合",
)
_BACKGROUND_TERMS = (
    "情报",
    "情報",
    "资料",
    "資料",
    "翻译资料",
    "翻譯資料",
    "访谈",
    "訪談",
    "新闻",
    "消息",
    "公布",
    "公开",
    "公開",
    "官网",
    "官網",
    "公式",
    "制作",
    "製作",
    "声优",
    "聲優",
    "先行图",
    "先行圖",
    "staff",
    "cast",
    "pv",
    "cm",
    "bd",
    "dvd",
)
_SOURCE_CATEGORIES = {"長篇連載", "长篇连载", "短篇漫畫", "短篇漫画", "[原创]", "[原創]", "[轻小说]", "[輕小說]"}
_DISCUSSION_CATEGORIES = {
    "[动画讨论]",
    "[動畫討論]",
    "[漫画讨论]",
    "[漫畫討論]",
    "[杂谈]",
    "[雜談]",
    "八卦杂谈",
    "八卦雜談",
    "[求推]",
    "[推荐]",
    "[推薦]",
    "[争议慎跳]",
    "[爭議慎跳]",
    "[其他]",
    "[2.5 次元]",
}
_BACKGROUND_CATEGORIES = {"[情报]", "[情報]", "[翻译资料]", "[翻譯資料]"}

_INLINE_EDIT_RE = re.compile(
    r"\s*本帖最后由\s+.{1,60}?\s+于\s+\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+编辑\s*",
    re.IGNORECASE,
)
_LAST_EDITED_BY_RE = re.compile(
    r"\s*Last\s+edited\s+by\s+.{1,80}?\s+on\s+\d{4}-\d{1,2}-\d{1,2}\s+at\s+\d{1,2}:\d{2}(?::\d{2})?\s*",
    re.IGNORECASE,
)
_TRUNCATED_LAST_EDITED_RE = re.compile(
    r"\[\s*Last\s+edited\s+by\s+.{1,120}?(?:\]|\.\.\.)",
    re.IGNORECASE,
)
_ORIG_POST_RE = re.compile(
    r"^\s*.{0,50}发表于\s+\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?\s*$",
    re.IGNORECASE,
)
_LEGACY_QUOTE_HEADER_RE = re.compile(r"^\s*原帖由\s+.{1,80}?\s+于\s+.{1,80}?\s+发表\s*$", re.IGNORECASE)
_INLINE_ORIGINAL_POST_PREFIX_RE = re.compile(
    r"(?:^|\n)\s*.{1,50}?\s+在\s+\d{4}[/-]\d{1,2}[/-]\d{1,2}\s+"
    r"(?:\d{1,2}:\d{2}(?:\s*(?:AM|PM))?|\d{1,2}:\d{2}\s*(?:AM|PM))\s*发表[:：]?\s*",
    re.IGNORECASE,
)
_INLINE_ORIGINAL_POST_FUZZY_PREFIX_RE = re.compile(
    r"(?:^|\n)\s*.{1,50}?\s+在\s+\d{4}[/-][\dXx]{1,2}[/-][\dXx]{1,2}\s+"
    r"[\dXx]{1,2}:[\dXx]{2}(?::[\dXx]{2})?(?:\s*(?:AM|PM))?\s*发表[:：]?\s*",
    re.IGNORECASE,
)
_INLINE_ORIGINAL_POST_ATTACHED_PREFIX_RE = re.compile(
    r"(?<=[\u4e00-\u9fff）)~～])[A-Za-z0-9_][A-Za-z0-9_\-]{1,29}\s+在\s+"
    r"\d{4}[/-]\d{1,2}[/-]\d{1,2}\s+\d{1,2}:\d{2}(?:\s*(?:AM|PM))?\s*发表[:：]\s*",
    re.IGNORECASE,
)
_INLINE_LEGACY_QUOTE_PREFIX_RE = re.compile(
    r"(?:^|\n)\s*原帖由\s*.{1,60}?\s+于\s+\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+"
    r"\d{1,2}:\d{2}(?:\s*(?:AM|PM))?\s*发表[。:：]?\s*",
    re.IGNORECASE,
)
_INLINE_LEGACY_QUOTE_LOOSE_PREFIX_RE = re.compile(
    r"(?:^|\n|\s)原帖由\s*.{1,60}?"
    r"(?:\s+于\s+(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2})"
    r"(?:\s+\d{1,2}:\d{2}(?:\s*(?:AM|PM))?)?)?\s*发表[。:：]?\s*",
    re.IGNORECASE,
)
_INLINE_LEGACY_QUOTE_ATTACHED_PREFIX_RE = re.compile(
    r"(?:\[code\]\s*|引用[:：]\s*)?原帖由\s*.{1,60}?"
    r"(?:\s+于\s+(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2})"
    r"(?:\s+\d{1,2}:\d{2}(?:\s*(?:AM|PM))?)?)?\s*发表[。:：]?\s*",
    re.IGNORECASE,
)
_INLINE_PUBLISHED_PREFIX_RE = re.compile(
    r"(^|\n|[。！？!?…]\s*)\s*[^\s。！？!?…，,；;：:]{1,50}\s+发表于\s+"
    r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?\s*",
    re.IGNORECASE,
)
_INLINE_PUBLISHED_TOOLBAR_RE = re.compile(
    r"\s*(?:[^\s。！？!?…，,；;：:]{1,50}\s+)?发表于\s+"
    r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?\s+资料\s+文集\s+短消息\s*",
    re.IGNORECASE,
)
_INLINE_PUBLISHED_AUTHOR_ATTACHED_RE = re.compile(
    r"(?:^|\n|\s|(?<=\])|(?<=[A-Za-z0-9_]))"
    r"[A-Za-z0-9_\-\u4e00-\u9fff]{0,30}\s*发表于\s+"
    r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?"
    r"(?:\s*\|\s*只看该作者)?\s*(?=\n|$)",
    re.IGNORECASE,
)
_INLINE_PUBLISHED_ATTACHED_NO_BREAK_RE = re.compile(
    r"(?<=[\u4e00-\u9fff）)])[A-Za-z0-9_][A-Za-z0-9_\-]{1,29}\s+发表于\s+"
    r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s*",
    re.IGNORECASE,
)
_INLINE_PUBLISHED_QUOTE_RE = re.compile(
    r"(?:(?<=\s)|(?<=[。！？!?…]))[^\s。！？!?…，,；;：:]{1,30}\s+发表于\s+"
    r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?\s*",
    re.IGNORECASE,
)
_YAMIBO_EMOJI_RE = re.compile(
    r"(?:yamibo(?:qe|hk)\d+|(?:yamibohu)+|(?:yamiboshiho)+|yamibon\d*)",
    re.IGNORECASE,
)
_LEGACY_LL_EMOJI_RE = re.compile(r"ll\d{1,3}", re.IGNORECASE)
_BBCODE_QUOTE_RE = re.compile(r"\[/?(?:quote|url|code)[^\]]*\]", re.IGNORECASE)
_HTML_STRUCTURAL_TAG_RE = re.compile(r"</?(?:quote|url)[^>]*>", re.IGNORECASE)
_REPLY_QUOTE_MARKUP_RE = re.compile(r"(?:\[/?quote[^\]]*\]|\[/quot\]|(?:^|\s)quote\])", re.IGNORECASE)
_REPLY_QUOTE_MARKER_RE = re.compile(r"引用[:：]\s*(?:原帖由|.{1,50}\s+(?:发表于|在\s+\d{4}[/-]))", re.IGNORECASE)
_REPLY_LEADING_QUOTE_HEADER_RE = re.compile(
    r"^\s*(?:原帖由\s*.{1,60}?\s+(?:于\s+)?|.{1,50}\s+)"
    r"(?:发表于|在)\s+\d{4}[/-]\d{1,2}[/-]\d{1,2}",
    re.IGNORECASE,
)
_EMPTY_BRACKET_RE = re.compile(r"\[\s*\]")
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)")
_SYMBOL_WALL_RE = re.compile(r"^[\s\-_=~*#·。.。・、|\\/:;,.，。!！?？]{8,}$")
_HAS_TEXT_SIGNAL_RE = re.compile(r"[\w\u4e00-\u9fff]")
_AUDIT_NOISE_PATTERNS: dict[str, re.Pattern[str]] = {
    "yamibo_emoji": _YAMIBO_EMOJI_RE,
    "cn_published": re.compile(r"发表于\s+\d{4}-\d{1,2}-\d{1,2}", re.IGNORECASE),
    "legacy_zai_published": re.compile(r"\s在\s+\d{4}[/-]\d{1,2}[/-]\d{1,2}[^\n]{0,30}发表", re.IGNORECASE),
    "orig_quote": re.compile(r"原帖由[^\n]{0,120}发表", re.IGNORECASE),
    "edit_notice": re.compile(r"本帖最后由|Last edited by", re.IGNORECASE),
    "legacy_ll_emoji": re.compile(r"ll\d{1,3}", re.IGNORECASE),
    "empty_bracket": re.compile(r"\[\s*\]"),
}

QuotePolicy = Literal["none", "brief", "preview_only", "long_suppressed"]
BodySource = Literal["reply_text", "content_fallback", "empty"]
QuoteSource = Literal["quote_text", "empty"]

QUOTE_BRIEF_MAX_CHARS = 160
QUOTE_PREVIEW_MAX_CHARS = 500
QUOTE_KEEP_PREVIEW_CHARS = 120
QUOTE_HEAVY_MIN_CHARS = 160
QUOTE_HEAVY_RATIO = 2.0


@dataclass(frozen=True)
class LaneClassification:
    lane: EvidenceLane
    confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CleanResult:
    text: str
    original_chars: int
    cleaned_chars: int
    changed: bool
    rules: tuple[str, ...]


@dataclass(frozen=True)
class AnimeDryRunChunk:
    chunk_id: str
    tid: int
    pid: int | None
    floor_no: int | None
    part_index: int
    chunk_type: str
    lane: EvidenceLane
    thread_lane: EvidenceLane
    text: str
    original_chars: int
    cleaned_chars: int
    clean_rules: tuple[str, ...]


@dataclass(frozen=True)
class FloorDryRunResult:
    tid: int
    pid: int
    floor_no: int
    publisher: str | None
    publisher_uid: str | None
    pub_time: str | None
    has_images: bool
    lane: EvidenceLane
    lane_confidence: float
    lane_reasons: tuple[str, ...]
    clean: CleanResult
    quote_clean: CleanResult | None
    body_source: BodySource
    quote_source: QuoteSource
    quote_policy: QuotePolicy
    quote_heavy_ratio: float
    structured_cleaning: bool
    chunk_count: int
    skipped_reason: str | None
    original_text: str
    original_reply_text: str
    original_quote_text: str
    original_preview: str
    original_reply_preview: str
    original_quote_preview: str
    cleaned_preview: str
    cleaned_quote_preview: str


@dataclass(frozen=True)
class ThreadDryRunResult:
    tid: int
    title: str
    category: str | None
    publisher: str | None
    pub_time: str | None
    thread_lane: EvidenceLane
    thread_confidence: float
    thread_reasons: tuple[str, ...]
    floor_count: int
    chunks: tuple[AnimeDryRunChunk, ...]
    floors: tuple[FloorDryRunResult, ...]
    source_hash: str = ""
    generated_at: str = ""


@dataclass(frozen=True)
class LaneChunkPolicy:
    min_chars: int
    max_chars: int
    index_candidate: bool


LANE_CHUNK_POLICIES: dict[EvidenceLane, LaneChunkPolicy] = {
    "discussion_evidence": LaneChunkPolicy(min_chars=8, max_chars=600, index_candidate=True),
    "unknown": LaneChunkPolicy(min_chars=8, max_chars=600, index_candidate=True),
    "background_material": LaneChunkPolicy(min_chars=20, max_chars=900, index_candidate=True),
    "source_text": LaneChunkPolicy(min_chars=80, max_chars=1500, index_candidate=True),
    "low_signal": LaneChunkPolicy(min_chars=0, max_chars=300, index_candidate=False),
}


def clean_anime_rag_text(value: str | None) -> CleanResult:
    original = value or ""
    rules: list[str] = []
    if _INLINE_EDIT_RE.search(original):
        rules.append("inline_edit_notice")
    if _LAST_EDITED_BY_RE.search(original):
        rules.append("last_edited_by")
    if _TRUNCATED_LAST_EDITED_RE.search(original) and "last_edited_by" not in rules:
        rules.append("last_edited_by")
    if any(_ORIG_POST_RE.match(line.strip()) for line in original.splitlines()):
        rules.append("orig_post_line")
    text = clean_content(original)
    if text != original.strip():
        rules.append("base_clean_content")

    before = text
    text = _YAMIBO_EMOJI_RE.sub("", text)
    if text != before:
        rules.append("yamibo_emoji")

    before = text
    text = _LEGACY_LL_EMOJI_RE.sub("", text)
    if text != before:
        rules.append("legacy_ll_emoji")

    before = text
    text = _INLINE_EDIT_RE.sub(" ", text)
    if text != before and "inline_edit_notice" not in rules:
        rules.append("inline_edit_notice")

    before = text
    text = _TRUNCATED_LAST_EDITED_RE.sub(" ", text)
    if text != before and "last_edited_by" not in rules:
        rules.append("last_edited_by")

    before = text
    text = _LAST_EDITED_BY_RE.sub(" ", text)
    if text != before and "last_edited_by" not in rules:
        rules.append("last_edited_by")

    before = text
    text = _BBCODE_QUOTE_RE.sub("", text)
    text = _HTML_STRUCTURAL_TAG_RE.sub("", text)
    if text != before:
        rules.append("bbcode_quote_tag")

    before = text
    text = _INLINE_LEGACY_QUOTE_PREFIX_RE.sub("\n", text)
    if text != before:
        rules.append("inline_legacy_quote_prefix")

    before = text
    text = _INLINE_LEGACY_QUOTE_LOOSE_PREFIX_RE.sub("\n", text)
    if text != before and "inline_legacy_quote_prefix" not in rules:
        rules.append("inline_legacy_quote_prefix")

    before = text
    text = _INLINE_LEGACY_QUOTE_ATTACHED_PREFIX_RE.sub(" ", text)
    if text != before and "inline_legacy_quote_prefix" not in rules:
        rules.append("inline_legacy_quote_prefix")

    before = text
    text = _INLINE_ORIGINAL_POST_ATTACHED_PREFIX_RE.sub(" ", text)
    if text != before and "inline_original_post_prefix" not in rules:
        rules.append("inline_original_post_prefix")

    before = text
    text = _INLINE_ORIGINAL_POST_PREFIX_RE.sub("\n", text)
    if text != before:
        rules.append("inline_original_post_prefix")

    before = text
    text = _INLINE_ORIGINAL_POST_FUZZY_PREFIX_RE.sub("\n", text)
    if text != before and "inline_original_post_prefix" not in rules:
        rules.append("inline_original_post_prefix")

    before = text
    text = _INLINE_PUBLISHED_TOOLBAR_RE.sub(" ", text)
    if text != before:
        rules.append("inline_published_prefix")

    before = text
    text = _INLINE_PUBLISHED_AUTHOR_ATTACHED_RE.sub("\n", text)
    if text != before and "inline_published_prefix" not in rules:
        rules.append("inline_published_prefix")

    before = text
    text = _INLINE_PUBLISHED_ATTACHED_NO_BREAK_RE.sub(" ", text)
    if text != before and "inline_published_prefix" not in rules:
        rules.append("inline_published_prefix")

    before = text
    text = _INLINE_PUBLISHED_PREFIX_RE.sub(lambda match: f"{match.group(1).rstrip()}\n", text)
    if text != before:
        rules.append("inline_published_prefix")

    before = text
    text = _INLINE_PUBLISHED_QUOTE_RE.sub("\n", text)
    if text != before and "inline_published_prefix" not in rules:
        rules.append("inline_published_prefix")

    before = text
    text = _EMPTY_BRACKET_RE.sub("", text)
    if text != before:
        rules.append("empty_bracket")

    before = text
    text = _EMPTY_PARENS_RE.sub("", text)
    if text != before:
        rules.append("empty_parens")

    kept_lines: list[str] = []
    removed_orig_post = False
    removed_legacy_quote = False
    removed_symbol_wall = False
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) <= 120 and _ORIG_POST_RE.match(stripped):
            removed_orig_post = True
            continue
        if len(stripped) <= 160 and _LEGACY_QUOTE_HEADER_RE.match(stripped):
            removed_legacy_quote = True
            continue
        if _SYMBOL_WALL_RE.match(stripped):
            removed_symbol_wall = True
            continue
        kept_lines.append(line)
    if removed_orig_post:
        rules.append("orig_post_line")
    if removed_legacy_quote:
        rules.append("legacy_quote_header")
    if removed_symbol_wall:
        rules.append("symbol_wall")
    text = "\n".join(kept_lines)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return CleanResult(
        text=text,
        original_chars=len(original),
        cleaned_chars=len(text),
        changed=text != original.strip(),
        rules=tuple(dict.fromkeys(rules)),
    )


def clean_anime_reply_text(value: str | None, *, fallback_from_content: bool = False) -> CleanResult:
    if fallback_from_content:
        return clean_anime_rag_text(value)
    if value and (
        _REPLY_QUOTE_MARKUP_RE.search(value)
        or _REPLY_QUOTE_MARKER_RE.search(value)
        or _REPLY_LEADING_QUOTE_HEADER_RE.search(value)
    ):
        return _clean_anime_text(value, remove_quote_headers=True)
    return _clean_anime_text(value, remove_quote_headers=False)


def clean_anime_quote_text(value: str | None) -> CleanResult:
    return _clean_anime_text(value, remove_quote_headers=True)


def _clean_anime_text(value: str | None, *, remove_quote_headers: bool) -> CleanResult:
    original = value or ""
    rules: list[str] = []
    if _INLINE_EDIT_RE.search(original):
        rules.append("inline_edit_notice")
    if _LAST_EDITED_BY_RE.search(original):
        rules.append("last_edited_by")
    if _TRUNCATED_LAST_EDITED_RE.search(original) and "last_edited_by" not in rules:
        rules.append("last_edited_by")

    text = clean_content(original)
    if text != original.strip():
        rules.append("base_clean_content")

    before = text
    text = _YAMIBO_EMOJI_RE.sub("", text)
    if text != before:
        rules.append("yamibo_emoji")

    before = text
    text = _LEGACY_LL_EMOJI_RE.sub("", text)
    if text != before:
        rules.append("legacy_ll_emoji")

    before = text
    text = _INLINE_EDIT_RE.sub(" ", text)
    if text != before and "inline_edit_notice" not in rules:
        rules.append("inline_edit_notice")

    before = text
    text = _TRUNCATED_LAST_EDITED_RE.sub(" ", text)
    if text != before and "last_edited_by" not in rules:
        rules.append("last_edited_by")

    before = text
    text = _LAST_EDITED_BY_RE.sub(" ", text)
    if text != before and "last_edited_by" not in rules:
        rules.append("last_edited_by")

    before = text
    text = _BBCODE_QUOTE_RE.sub("", text)
    text = _HTML_STRUCTURAL_TAG_RE.sub("", text)
    if text != before:
        rules.append("bbcode_quote_tag")

    if remove_quote_headers:
        before = text
        text = _INLINE_LEGACY_QUOTE_PREFIX_RE.sub("\n", text)
        text = _INLINE_LEGACY_QUOTE_LOOSE_PREFIX_RE.sub("\n", text)
        text = _INLINE_LEGACY_QUOTE_ATTACHED_PREFIX_RE.sub(" ", text)
        text = _INLINE_ORIGINAL_POST_ATTACHED_PREFIX_RE.sub(" ", text)
        text = _INLINE_ORIGINAL_POST_PREFIX_RE.sub("\n", text)
        text = _INLINE_ORIGINAL_POST_FUZZY_PREFIX_RE.sub("\n", text)
        text = _INLINE_PUBLISHED_TOOLBAR_RE.sub(" ", text)
        text = _INLINE_PUBLISHED_AUTHOR_ATTACHED_RE.sub("\n", text)
        text = _INLINE_PUBLISHED_ATTACHED_NO_BREAK_RE.sub(" ", text)
        text = _INLINE_PUBLISHED_PREFIX_RE.sub(lambda match: f"{match.group(1).rstrip()}\n", text)
        text = _INLINE_PUBLISHED_QUOTE_RE.sub("\n", text)
        if text != before:
            rules.append("quote_header")

    before = text
    text = _EMPTY_BRACKET_RE.sub("", text)
    if text != before:
        rules.append("empty_bracket")

    before = text
    text = _EMPTY_PARENS_RE.sub("", text)
    if text != before:
        rules.append("empty_parens")

    kept_lines: list[str] = []
    removed_symbol_wall = False
    removed_legacy_quote = False
    removed_orig_post = False
    for line in text.splitlines():
        stripped = line.strip()
        if remove_quote_headers and len(stripped) <= 120 and _ORIG_POST_RE.match(stripped):
            removed_orig_post = True
            continue
        if remove_quote_headers and len(stripped) <= 160 and _LEGACY_QUOTE_HEADER_RE.match(stripped):
            removed_legacy_quote = True
            continue
        if _SYMBOL_WALL_RE.match(stripped):
            removed_symbol_wall = True
            continue
        kept_lines.append(line)
    if removed_orig_post:
        rules.append("orig_post_line")
    if removed_legacy_quote:
        rules.append("legacy_quote_header")
    if removed_symbol_wall:
        rules.append("symbol_wall")
    text = "\n".join(kept_lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return CleanResult(
        text=text,
        original_chars=len(original),
        cleaned_chars=len(text),
        changed=text != original.strip(),
        rules=tuple(dict.fromkeys(rules)),
    )


def classify_anime_thread(thread_row: Any, floor_rows: Iterable[Any] | None = None) -> LaneClassification:
    title = _row_text(thread_row, "display_title") or _row_text(thread_row, "raw_title")
    category = _row_text(thread_row, "category")
    haystack = f"{category} {title}".lower()
    reasons: list[str] = []

    if _contains_any(haystack, _DISCUSSION_TERMS):
        reasons.append("title_or_category_discussion_marker")
        return LaneClassification("discussion_evidence", 0.88, tuple(reasons))
    if category in _DISCUSSION_CATEGORIES:
        reasons.append("discussion_category")
        return LaneClassification("discussion_evidence", 0.82, tuple(reasons))
    if category in _BACKGROUND_CATEGORIES or _contains_any(haystack, _BACKGROUND_TERMS):
        reasons.append("background_marker")
        return LaneClassification("background_material", 0.80, tuple(reasons))
    if category in _SOURCE_CATEGORIES:
        reasons.append("source_category_without_discussion_marker")
        return LaneClassification("source_text", 0.78, tuple(reasons))

    floors = list(floor_rows or [])
    non_empty_lengths = [len(str(_row_get(row, "content") or "").strip()) for row in floors if str(_row_get(row, "content") or "").strip()]
    if non_empty_lengths:
        short_ratio = sum(1 for length in non_empty_lengths if length <= 220) / len(non_empty_lengths)
        long_ratio = sum(1 for length in non_empty_lengths if length >= 1200) / len(non_empty_lengths)
        if len(non_empty_lengths) >= 3 and short_ratio >= 0.70:
            reasons.append("floor_shape_short_interactive")
            return LaneClassification("discussion_evidence", 0.70, tuple(reasons))
        if long_ratio >= 0.50 and len(non_empty_lengths) <= 5:
            reasons.append("floor_shape_long_source_like")
            return LaneClassification("source_text", 0.64, tuple(reasons))

    reasons.append("insufficient_thread_signal")
    return LaneClassification("unknown", 0.50, tuple(reasons))


def classify_anime_floor(
    *,
    thread_lane: EvidenceLane,
    text: str,
    floor_no: int,
    has_images: bool | None = None,
) -> LaneClassification:
    stripped = text.strip()
    lower = stripped.lower()
    if not stripped:
        return LaneClassification("low_signal", 0.99, ("empty_after_clean",))
    if len(stripped) < 8:
        return LaneClassification("low_signal", 0.86, ("too_short",))
    if not _HAS_TEXT_SIGNAL_RE.search(stripped):
        return LaneClassification("low_signal", 0.90, ("no_text_signal",))
    if _mostly_repeated_punctuation(stripped):
        return LaneClassification("low_signal", 0.82, ("mostly_punctuation",))
    if _contains_any(lower, _DISCUSSION_TERMS):
        return LaneClassification("discussion_evidence", 0.84, ("floor_discussion_marker",))
    if thread_lane == "source_text" and len(stripped) >= 80:
        return LaneClassification("source_text", 0.76, ("thread_source_lane",))
    if len(stripped) >= 1600 and thread_lane not in {"discussion_evidence", "unknown"}:
        return LaneClassification("source_text", 0.62, ("very_long_non_discussion_floor",))
    if thread_lane == "background_material":
        return LaneClassification("background_material", 0.74, ("thread_background_lane",))
    if has_images and floor_no == 1 and _contains_any(lower, _BACKGROUND_TERMS):
        return LaneClassification("background_material", 0.68, ("image_first_floor_background_marker",))
    if thread_lane in {"discussion_evidence", "unknown"}:
        return LaneClassification("discussion_evidence", 0.66 if thread_lane == "unknown" else 0.78, ("thread_discussion_or_unknown_lane",))
    return LaneClassification(thread_lane, 0.55, ("thread_lane_fallback",))


def build_anime_dry_run_thread(
    *,
    thread_row: Any,
    floor_rows: list[Any],
    include_low_signal: bool = False,
    structured_cleaning: bool = False,
) -> ThreadDryRunResult:
    thread_classification = classify_anime_thread(thread_row, floor_rows)
    tid = int(_row_get(thread_row, "tid"))
    title = (_row_text(thread_row, "display_title") or _row_text(thread_row, "raw_title")).strip()
    chunks: list[AnimeDryRunChunk] = []
    floors: list[FloorDryRunResult] = []

    if title:
        title_policy = LANE_CHUNK_POLICIES[thread_classification.lane]
        if title_policy.index_candidate or include_low_signal:
            chunks.append(
                AnimeDryRunChunk(
                    chunk_id=f"thread:{tid}:title:{DRY_RUN_VERSION}",
                    tid=tid,
                    pid=None,
                    floor_no=None,
                    part_index=1,
                    chunk_type="thread_title",
                    lane=thread_classification.lane,
                    thread_lane=thread_classification.lane,
                    text=title,
                    original_chars=len(title),
                    cleaned_chars=len(title),
                    clean_rules=(),
                )
            )

    for floor_row in floor_rows:
        pid = int(_row_get(floor_row, "pid"))
        floor_no = int(_row_get(floor_row, "floor_no"))
        original_floor_text = _row_get(floor_row, "content") or ""
        original_reply_text = _row_get(floor_row, "reply_text") or ""
        original_quote_text = _row_get(floor_row, "quote_text") or ""
        body_source: BodySource = "empty"
        if structured_cleaning and str(original_reply_text).strip():
            body_source = "reply_text"
            clean = clean_anime_reply_text(original_reply_text)
        elif structured_cleaning and str(original_floor_text).strip():
            body_source = "content_fallback"
            clean = clean_anime_reply_text(original_floor_text, fallback_from_content=True)
        else:
            body_source = "content_fallback" if str(original_floor_text).strip() else "empty"
            clean = clean_anime_rag_text(original_floor_text)
        quote_source: QuoteSource = "quote_text" if structured_cleaning and str(original_quote_text).strip() else "empty"
        quote_clean = clean_anime_quote_text(original_quote_text) if quote_source == "quote_text" else None
        quote_policy = _quote_policy(quote_clean.text if quote_clean is not None else "")
        quote_heavy_ratio = _quote_heavy_ratio(
            clean_quote_chars=quote_clean.cleaned_chars if quote_clean is not None else 0,
            clean_body_chars=clean.cleaned_chars,
        )
        floor_classification = classify_anime_floor(
            thread_lane=thread_classification.lane,
            text=clean.text,
            floor_no=floor_no,
            has_images=bool(_row_get(floor_row, "has_images")),
        )
        policy = LANE_CHUNK_POLICIES[floor_classification.lane]
        skipped_reason = None
        floor_chunk_count = 0
        if not policy.index_candidate and not include_low_signal:
            skipped_reason = "low_signal_excluded"
        elif len(clean.text) < policy.min_chars:
            skipped_reason = f"below_min_chars:{policy.min_chars}"
        else:
            parts = split_text_for_embedding(clean.text, max_chunk_chars=policy.max_chars)
            for part_index, part in enumerate(parts, start=1):
                if len(part.strip()) < policy.min_chars:
                    continue
                floor_chunk_count += 1
                chunks.append(
                    AnimeDryRunChunk(
                        chunk_id=f"thread:{tid}:floor:{floor_no}:part:{part_index}:{DRY_RUN_VERSION}",
                        tid=tid,
                        pid=pid,
                        floor_no=floor_no,
                        part_index=part_index,
                        chunk_type="floor",
                        lane=floor_classification.lane,
                        thread_lane=thread_classification.lane,
                        text=part.strip(),
                        original_chars=clean.original_chars,
                        cleaned_chars=clean.cleaned_chars,
                        clean_rules=clean.rules,
                    )
                )
            if floor_chunk_count == 0 and skipped_reason is None:
                skipped_reason = "no_part_after_split"

        floors.append(
            FloorDryRunResult(
                tid=tid,
                pid=pid,
                floor_no=floor_no,
                publisher=_optional_text(_row_get(floor_row, "publisher")),
                publisher_uid=_optional_text(_row_get(floor_row, "publisher_uid")),
                pub_time=_jsonable_datetime(_row_get(floor_row, "pub_time")),
                has_images=bool(_row_get(floor_row, "has_images")),
                lane=floor_classification.lane,
                lane_confidence=floor_classification.confidence,
                lane_reasons=floor_classification.reasons,
                clean=clean,
                quote_clean=quote_clean,
                body_source=body_source,
                quote_source=quote_source,
                quote_policy=quote_policy,
                quote_heavy_ratio=quote_heavy_ratio,
                structured_cleaning=structured_cleaning,
                chunk_count=floor_chunk_count,
                skipped_reason=skipped_reason,
                original_text=original_floor_text,
                original_reply_text=original_reply_text,
                original_quote_text=original_quote_text,
                original_preview=_preview(original_floor_text),
                original_reply_preview=_preview(original_reply_text),
                original_quote_preview=_preview(original_quote_text),
                cleaned_preview=_preview(clean.text),
                cleaned_quote_preview=_preview(quote_clean.text if quote_clean is not None else ""),
            )
        )

    source_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "tid": tid,
                "thread_lane": thread_classification.lane,
                "thread_reasons": list(thread_classification.reasons),
                "floors": [
                    {
                        "pid": floor.pid,
                        "floor_no": floor.floor_no,
                        "publisher": floor.publisher,
                        "pub_time": floor.pub_time,
                        "content": floor.original_text,
                        "reply_text": floor.original_reply_text,
                        "quote_text": floor.original_quote_text,
                        "has_images": floor.has_images,
                    }
                    for floor in floors
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return ThreadDryRunResult(
        tid=tid,
        title=title,
        category=_optional_text(_row_get(thread_row, "category")),
        publisher=_optional_text(_row_get(thread_row, "publisher")),
        pub_time=_jsonable_datetime(_row_get(thread_row, "pub_time")),
        thread_lane=thread_classification.lane,
        thread_confidence=thread_classification.confidence,
        thread_reasons=thread_classification.reasons,
        floor_count=len(floor_rows),
        chunks=tuple(chunks),
        floors=tuple(floors),
        source_hash=source_fingerprint,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _quote_policy(text: str) -> QuotePolicy:
    length = len(text.strip())
    if length <= 0:
        return "none"
    if length <= QUOTE_BRIEF_MAX_CHARS:
        return "brief"
    if length <= QUOTE_PREVIEW_MAX_CHARS:
        return "preview_only"
    return "long_suppressed"


def _quote_heavy_ratio(*, clean_quote_chars: int, clean_body_chars: int) -> float:
    if clean_quote_chars <= 0:
        return 0.0
    return round(clean_quote_chars / max(clean_body_chars, 1), 4)


def run_anime_rag_dry_run_audit(
    *,
    forum_id: int = ANIME_FORUM_ID,
    sample_size: int = 240,
    output_dir: Path | None = None,
    include_low_signal: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    if settings.db_backend != "postgres":
        raise ValueError("anime RAG dry-run audit is PostgreSQL-first and requires database.backend=postgres")
    output_dir = output_dir or (settings.project_root / ".omx" / "reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(settings)
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        corpus_stats = _load_corpus_stats(conn, forum_id=forum_id)
        noise_stats = _load_noise_stats(conn, forum_id=forum_id)
        category_stats = _load_category_stats(conn, forum_id=forum_id, limit=18)
        thread_rows = _sample_threads(conn, forum_id=forum_id, sample_size=sample_size)
        floor_rows_by_tid = _load_floors(conn, [int(row["tid"]) for row in thread_rows])
    finally:
        conn.close()

    thread_results = [
        build_anime_dry_run_thread(
            thread_row=thread_row,
            floor_rows=floor_rows_by_tid.get(int(thread_row["tid"]), []),
            include_low_signal=include_low_signal,
        )
        for thread_row in thread_rows
    ]
    audit = _summarize_audit(
        forum_id=forum_id,
        sample_size=sample_size,
        include_low_signal=include_low_signal,
        corpus_stats=corpus_stats,
        noise_stats=noise_stats,
        category_stats=category_stats,
        thread_results=thread_results,
    )

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    base_name = f"anime-rag-evidence-lane-dry-run-{timestamp}"
    json_path = output_dir / f"{base_name}.json"
    md_path = output_dir / f"{base_name}.md"
    atomic_write_text(json_path, json.dumps(audit, ensure_ascii=False, indent=2, default=_json_default) + "\n")
    atomic_write_text(md_path, render_audit_markdown(audit))
    audit["artifacts"] = {"json": str(json_path), "markdown": str(md_path)}
    return audit


def render_audit_markdown(audit: dict[str, Any]) -> str:
    sample = audit["sample"]
    corpus = audit["corpus"]
    lines = [
        "# 动漫区 Evidence Lane Dry-run 审计报告",
        "",
        f"- 生成时间：{audit['generated_at']}",
        f"- dry-run 版本：`{audit['dry_run_version']}`",
        f"- 分区：`forum_id={audit['forum_id']}`",
        f"- 有效归档状态：`{', '.join(VALID_ARCHIVE_STATUSES)}`",
        f"- 样本线程：{sample['thread_count']}",
        f"- 样本楼层：{sample['floor_count']}",
        f"- 候选 chunk：{sample['candidate_chunk_count']}",
        "",
        "## 1. 归档库总体统计",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 有效线程数 | {corpus['valid_threads']} |",
        f"| 非空楼层数 | {corpus['nonempty_floors']} |",
        f"| 楼层长度 p50 | {corpus.get('floor_len_p50') or '-'} |",
        f"| 楼层长度 p90 | {corpus.get('floor_len_p90') or '-'} |",
        f"| 楼层长度 p99 | {corpus.get('floor_len_p99') or '-'} |",
        f"| 最大楼层长度 | {corpus.get('floor_len_max') or '-'} |",
        "",
        "## 2. 原始噪声命中",
        "",
        "| 噪声 | 命中楼层 |",
        "|---|---:|",
    ]
    for key, value in audit["noise"].items():
        lines.append(f"| `{key}` | {value} |")

    lines.extend(
        [
            "",
            "## 3. 类别采样背景",
            "",
            "| 类别 | 线程数 |",
            "|---|---:|",
        ]
    )
    for row in audit["category_stats"]:
        lines.append(f"| {row['category'] or '(none)'} | {row['thread_count']} |")

    lines.extend(
        [
            "",
            "## 4. Evidence lane 分布",
            "",
            "### 4.1 线程 lane",
            "",
            "| lane | 线程数 |",
            "|---|---:|",
        ]
    )
    for lane, count in audit["sample"]["thread_lane_counts"].items():
        lines.append(f"| `{lane}` | {count} |")
    lines.extend(["", "### 4.2 楼层 lane", "", "| lane | 楼层数 |", "|---|---:|"])
    for lane, count in audit["sample"]["floor_lane_counts"].items():
        lines.append(f"| `{lane}` | {count} |")
    lines.extend(["", "### 4.3 chunk lane", "", "| lane | chunk 数 |", "|---|---:|"])
    for lane, count in audit["sample"]["chunk_lane_counts"].items():
        lines.append(f"| `{lane}` | {count} |")

    clean = audit["cleaning"]
    lines.extend(
        [
            "",
            "## 5. 清洗效果",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| 发生变化的楼层 | {clean['changed_floors']} |",
            f"| 清洗后为空 | {clean['emptied_floors']} |",
            f"| 原始总字符 | {clean['original_chars']} |",
            f"| 清洗后总字符 | {clean['cleaned_chars']} |",
            f"| 收缩比例 | {clean['shrink_ratio']:.2%} |",
            "",
            "### 5.1 规则命中",
            "",
            "| 规则 | 命中楼层 |",
            "|---|---:|",
        ]
    )
    for rule, count in clean["rule_hits"].items():
        lines.append(f"| `{rule}` | {count} |")

    lines.extend(
        [
            "",
            "### 5.2 样本噪声清理前后对比",
            "",
            "| 噪声 | 清洗前命中楼层 | 清洗后残留楼层 |",
            "|---|---:|---:|",
        ]
    )
    sample_before = clean.get("sample_noise_before", {})
    sample_after = clean.get("sample_noise_after", {})
    for key in sorted(set(sample_before) | set(sample_after)):
        lines.append(f"| `{key}` | {sample_before.get(key, 0)} | {sample_after.get(key, 0)} |")

    lines.extend(
        [
            "",
            "### 5.3 清洗样例",
            "",
        ]
    )
    for item in clean["examples"]:
        lines.extend(
            [
                f"#### TID {item['tid']} / floor {item['floor_no']} / `{item['lane']}`",
                "",
                "清洗前：",
                "",
                "```text",
                item["original_preview"],
                "```",
                "",
                "清洗后：",
                "",
                "```text",
                item["cleaned_preview"],
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## 6. 初步判别",
            "",
            "- `discussion_evidence` 与 `unknown` 应作为趋势 evidence 的第一召回池。",
            "- `background_material` 适合作为事实背景补充，不应与讨论观点同权排序。",
            "- `source_text` 应默认降权或排除出趋势观点池，避免漫画正文、轻小说正文、转载正文挤占用户讨论证据。",
            "- `low_signal` 本轮默认不进入候选 chunk，但保留审计计数，用于观察短回复和纯噪声的边界。",
            "",
            "## 7. 后续实现建议",
            "",
            "1. 先以 metadata-only 方式在 `rag_chunks.metadata_text` 或伴随 JSON 中带上 `evidence_lane/chunker_version/cleaner_version`，验证趋势查询收益。",
            "2. 稳定后再添加 PostgreSQL schema 字段：`evidence_lane`、`cleaner_version`、`chunker_version`、`quality_flags_jsonb`、`search_vector`。",
            "3. 趋势查询优先筛选 `evidence_lane IN ('discussion_evidence', 'unknown')`，再按问题类型补充 `background_material`。",
            "4. PostgreSQL keyword path 应从无排名 `ILIKE` 逐步升级为 `tsvector + pg_trgm` 组合，避免 evidence 排序只依赖插入顺序。",
            "",
        ]
    )
    return "\n".join(lines)


def _summarize_audit(
    *,
    forum_id: int,
    sample_size: int,
    include_low_signal: bool,
    corpus_stats: dict[str, Any],
    noise_stats: dict[str, int],
    category_stats: list[dict[str, Any]],
    thread_results: list[ThreadDryRunResult],
) -> dict[str, Any]:
    thread_lane_counts = Counter(result.thread_lane for result in thread_results)
    floor_lane_counts: Counter[str] = Counter()
    chunk_lane_counts: Counter[str] = Counter()
    rule_hits: Counter[str] = Counter()
    sample_noise_before: Counter[str] = Counter()
    sample_noise_after: Counter[str] = Counter()
    changed_floors = 0
    emptied_floors = 0
    original_chars = 0
    cleaned_chars = 0
    examples: list[dict[str, Any]] = []

    for result in thread_results:
        for chunk in result.chunks:
            chunk_lane_counts[chunk.lane] += 1
        for floor in result.floors:
            floor_lane_counts[floor.lane] += 1
            for noise_name, pattern in _AUDIT_NOISE_PATTERNS.items():
                if pattern.search(floor.original_text):
                    sample_noise_before[noise_name] += 1
                if pattern.search(floor.clean.text):
                    sample_noise_after[noise_name] += 1
            original_chars += floor.clean.original_chars
            cleaned_chars += floor.clean.cleaned_chars
            if floor.clean.changed:
                changed_floors += 1
                for rule in floor.clean.rules:
                    rule_hits[rule] += 1
                if len(examples) < 12:
                    examples.append(
                        {
                            "tid": floor.tid,
                            "floor_no": floor.floor_no,
                            "lane": floor.lane,
                            "rules": list(floor.clean.rules),
                            "original_preview": floor.original_preview,
                            "cleaned_preview": floor.cleaned_preview,
                        }
                    )
            if floor.clean.original_chars and not floor.clean.text:
                emptied_floors += 1

    floor_count = sum(len(result.floors) for result in thread_results)
    shrink_ratio = 0.0 if original_chars <= 0 else max((original_chars - cleaned_chars) / original_chars, 0.0)
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "dry_run_version": DRY_RUN_VERSION,
        "forum_id": forum_id,
        "requested_sample_size": sample_size,
        "include_low_signal": include_low_signal,
        "corpus": corpus_stats,
        "noise": noise_stats,
        "category_stats": category_stats,
        "sample": {
            "thread_count": len(thread_results),
            "floor_count": floor_count,
            "candidate_chunk_count": sum(len(result.chunks) for result in thread_results),
            "thread_lane_counts": dict(sorted(thread_lane_counts.items())),
            "floor_lane_counts": dict(sorted(floor_lane_counts.items())),
            "chunk_lane_counts": dict(sorted(chunk_lane_counts.items())),
        },
        "cleaning": {
            "changed_floors": changed_floors,
            "emptied_floors": emptied_floors,
            "original_chars": original_chars,
            "cleaned_chars": cleaned_chars,
            "shrink_ratio": shrink_ratio,
            "rule_hits": dict(rule_hits.most_common()),
            "sample_noise_before": dict(sample_noise_before.most_common()),
            "sample_noise_after": dict(sample_noise_after.most_common()),
            "examples": examples,
        },
        "threads": [_thread_result_summary(result) for result in thread_results[:80]],
    }


def _thread_result_summary(result: ThreadDryRunResult) -> dict[str, Any]:
    return {
        "tid": result.tid,
        "title": result.title,
        "category": result.category,
        "thread_lane": result.thread_lane,
        "thread_confidence": result.thread_confidence,
        "thread_reasons": list(result.thread_reasons),
        "floor_count": result.floor_count,
        "chunk_count": len(result.chunks),
        "floor_lane_counts": dict(Counter(floor.lane for floor in result.floors)),
    }


def _load_corpus_stats(conn: Any, *, forum_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
          COUNT(DISTINCT t.tid) AS valid_threads,
          COUNT(f.pid) FILTER (WHERE NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL) AS nonempty_floors,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY char_length(f.content))
            FILTER (WHERE NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL) AS floor_len_p50,
          percentile_cont(0.9) WITHIN GROUP (ORDER BY char_length(f.content))
            FILTER (WHERE NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL) AS floor_len_p90,
          percentile_cont(0.99) WITHIN GROUP (ORDER BY char_length(f.content))
            FILTER (WHERE NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL) AS floor_len_p99,
          MAX(char_length(f.content)) FILTER (WHERE NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL) AS floor_len_max
        FROM threads t
        LEFT JOIN floors f ON f.tid = t.tid
        WHERE t.forum_id = ? AND t.archive_status IN ('complete', 'partial')
        """,
        (forum_id,),
    ).fetchone()
    return {key: _round_float(row[key]) for key in row.keys()} if row else {}


def _load_noise_stats(conn: Any, *, forum_id: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          COUNT(*) FILTER (
            WHERE f.content ~* '(yamibo(qe|hk)[0-9]+|(yamibohu)+|(yamiboshiho)+|yamibon[0-9]*)'
          ) AS yamibo_emoji,
          COUNT(*) FILTER (WHERE f.content LIKE '%发表于%') AS orig_post_line,
          COUNT(*) FILTER (WHERE f.content LIKE '%本帖最后由%') AS edit_notice,
          COUNT(*) FILTER (WHERE f.content LIKE '%下载附件%') AS attachment_residue,
          COUNT(*) FILTER (WHERE f.content ~ '^[[:space:]\\-_=~*#·。.。・、|\\\\/:;,.，。!！?？]{8,}$') AS symbol_wall
        FROM threads t
        JOIN floors f ON f.tid = t.tid
        WHERE t.forum_id = ? AND t.archive_status IN ('complete', 'partial')
          AND NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL
        """,
        (forum_id,),
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()} if row else {}


def _load_category_stats(conn: Any, *, forum_id: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(t.category, ''), '(none)') AS category, COUNT(*) AS thread_count
        FROM threads t
        WHERE t.forum_id = ? AND t.archive_status IN ('complete', 'partial')
        GROUP BY COALESCE(NULLIF(t.category, ''), '(none)')
        ORDER BY COUNT(*) DESC, category ASC
        LIMIT ?
        """,
        (forum_id, limit),
    ).fetchall()
    return [{"category": row["category"], "thread_count": int(row["thread_count"])} for row in rows]


def _sample_threads(conn: Any, *, forum_id: int, sample_size: int) -> list[Any]:
    category_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(t.category, ''), '(none)') AS category, COUNT(*) AS c
        FROM threads t
        WHERE t.forum_id = ? AND t.archive_status IN ('complete', 'partial')
        GROUP BY COALESCE(NULLIF(t.category, ''), '(none)')
        ORDER BY COUNT(*) DESC
        LIMIT 12
        """,
        (forum_id,),
    ).fetchall()
    per_category = max(sample_size // max(len(category_rows), 1), 1)
    sampled: dict[int, Any] = {}
    for category_row in category_rows:
        category = category_row["category"]
        if category == "(none)":
            rows = conn.execute(
                _THREAD_SAMPLE_SELECT
                + """
                WHERE t.forum_id = ? AND t.archive_status IN ('complete', 'partial')
                  AND (t.category IS NULL OR t.category = '')
                GROUP BY t.tid
                ORDER BY random()
                LIMIT ?
                """,
                (forum_id, per_category),
            ).fetchall()
        else:
            rows = conn.execute(
                _THREAD_SAMPLE_SELECT
                + """
                WHERE t.forum_id = ? AND t.archive_status IN ('complete', 'partial')
                  AND t.category = ?
                GROUP BY t.tid
                ORDER BY random()
                LIMIT ?
                """,
                (forum_id, category, per_category),
            ).fetchall()
        for row in rows:
            sampled[int(row["tid"])] = row
    remaining = max(sample_size - len(sampled), 0)
    if remaining:
        existing = list(sampled.keys()) or [-1]
        existing_placeholders = ",".join("?" for _ in existing)
        rows = conn.execute(
            _THREAD_SAMPLE_SELECT
            + f"""
            WHERE t.forum_id = ? AND t.archive_status IN ('complete', 'partial')
              AND t.tid NOT IN ({existing_placeholders})
            GROUP BY t.tid
            ORDER BY random()
            LIMIT ?
            """,
            (forum_id, *existing, remaining),
        ).fetchall()
        for row in rows:
            sampled[int(row["tid"])] = row
    return list(sampled.values())[:sample_size]


_THREAD_SAMPLE_SELECT = """
    SELECT
      t.tid, t.raw_title, t.display_title, t.publisher, t.pub_time, t.forum_id,
      t.content_kind, t.category, t.series_id,
      COUNT(f.pid) AS floor_count,
      COUNT(f.pid) FILTER (WHERE NULLIF(BTRIM(COALESCE(f.content, '')), '') IS NOT NULL) AS nonempty_floor_count
    FROM threads t
    LEFT JOIN floors f ON f.tid = t.tid
"""


def _load_floors(conn: Any, tids: list[int]) -> dict[int, list[Any]]:
    if not tids:
        return {}
    placeholders = ",".join("?" for _ in tids)
    rows = conn.execute(
        f"""
        SELECT pid, tid, floor_no, publisher, publisher_uid, pub_time, content, has_images, quote_text, reply_text
        FROM floors
        WHERE tid IN ({placeholders})
        ORDER BY tid ASC, floor_no ASC, pid ASC
        """,
        tuple(tids),
    ).fetchall()
    grouped: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[int(row["tid"])].append(row)
    return grouped


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(term.lower() in lowered for term in terms)


def _mostly_repeated_punctuation(value: str) -> bool:
    if len(value) < 12:
        return False
    signal_chars = len(_HAS_TEXT_SIGNAL_RE.findall(value))
    return signal_chars / max(len(value), 1) < 0.15


def _row_get(row: Any, key: str) -> Any:
    if hasattr(row, "get"):
        return row.get(key)
    return row[key]


def _row_text(row: Any, key: str) -> str:
    value = _row_get(row, key)
    return "" if value is None else str(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _jsonable_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _preview(value: str, *, limit: int = 160) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:limit] + ("..." if len(normalized) > limit else "")


def _round_float(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "_asdict"):
        return value._asdict()
    if hasattr(value, "__dict__"):
        return asdict(value)
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run anime forum RAG evidence-lane dry-run audit without writing DB rows.")
    parser.add_argument("--forum-id", type=int, default=ANIME_FORUM_ID, help="Forum id to audit; V1 is designed for forum_id=5.")
    parser.add_argument("--sample-size", type=int, default=240, help="Number of valid archived threads to sample.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for JSON and Markdown reports.")
    parser.add_argument("--include-low-signal", action="store_true", help="Include low_signal floors as chunks in dry-run output.")
    args = parser.parse_args(argv)
    audit = run_anime_rag_dry_run_audit(
        forum_id=args.forum_id,
        sample_size=args.sample_size,
        output_dir=args.output_dir,
        include_low_signal=args.include_low_signal,
    )
    artifacts = audit.get("artifacts", {})
    print(json.dumps({"ok": True, "artifacts": artifacts, "sample": audit["sample"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
