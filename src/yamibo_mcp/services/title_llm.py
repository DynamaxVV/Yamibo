from __future__ import annotations

import json
import logging
import re
from typing import Any

from yamibo_mcp.config import Settings
from yamibo_mcp.services.title_hints import load_title_hints
from yamibo_mcp.services.llm_client import LLMRequestError, openai_compatible_chat
from yamibo_mcp.yamibo.title.normalizer import normalize_display_title, normalize_series_key
from yamibo_mcp.yamibo.title.parser import TitleParseResult, parse_title


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
LOG = logging.getLogger(__name__)


def should_refine_title_with_llm(parsed: TitleParseResult) -> bool:
    return parsed.needs_review or parsed.confidence < 0.75 or not parsed.series_key or len(parsed.core_title_guess) <= 3


def should_parse_title_with_llm(settings: Settings, parsed: TitleParseResult) -> bool:
    mode = _title_parse_mode(settings)
    if not settings.llm_api_key or mode == "rules_only":
        return False
    if mode == "always":
        return True
    return should_refine_title_with_llm(parsed)


def _title_parse_mode(settings: Settings) -> str:
    mode = getattr(settings, "title_parse_mode", None)
    if mode is None:
        return "always" if settings.title_parse_use_llm else "rules_only"
    if mode not in {"rules_only", "fallback", "always"}:
        raise ValueError("title.parse_mode must be rules_only, fallback or always")
    return mode


def refine_title_parse_with_llm(
    settings: Settings,
    *,
    raw_title: str,
    parsed: TitleParseResult | None = None,
) -> tuple[TitleParseResult, dict[str, object] | None]:
    base = parsed or parse_title(raw_title)
    if not should_parse_title_with_llm(settings, base):
        return base, None

    prompt = _build_title_parse_prompt(settings, base)
    meta: dict[str, object] = {
        "attempted": True,
        "used": False,
        "strategy": "llm_primary" if _title_parse_mode(settings) == "always" else "llm_fallback",
        "model": settings.llm_model,
        "raw_title": raw_title,
        "baseline": title_parse_to_dict(base),
    }
    try:
        result = openai_compatible_chat(
            settings,
            system_prompt=prompt,
            user_prompt=f"标题：{raw_title}",
            temperature=0.0,
        )
        print("Title LLM parse result: %s", str(result["content"]))
        payload = _parse_json_payload(str(result["content"]))
        refined = _merge_title_payload(raw_title=raw_title, base=base, payload=payload)
        llm_meta = {
            **meta,
            "model": result["model"],
            "used": True,
            "raw_content": result["content"],
            "payload": payload,
            "result": title_parse_to_dict(refined),
        }
        LOG.info(
            "Title LLM parse succeeded strategy=%s model=%s series_key=%s chapter_name=%s chapter_title=%s review=%s",
            llm_meta["strategy"],
            llm_meta["model"],
            refined.series_key,
            refined.chapter_name,
            refined.chapter_title,
            refined.needs_review,
        )
        return refined, llm_meta
    except (LLMRequestError, OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        meta["error"] = {"type": exc.__class__.__name__, "message": str(exc)}
        meta["result"] = title_parse_to_dict(base)
        LOG.warning(
            "Title LLM parse failed strategy=%s model=%s error=%s: %s",
            meta["strategy"],
            meta["model"],
            exc.__class__.__name__,
            exc,
        )
        return base, meta


def _parse_json_payload(content: str) -> dict[str, object]:
    text = content.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _build_title_parse_prompt(settings: Settings, base: TitleParseResult) -> str:
    hints_payload = load_title_hints(settings)
    hints = {
        "common_scanlation_groups": hints_payload["scanlation_groups"],
        "common_authors": hints_payload["authors"],
        "rule_parse_baseline": {
            "group_name": base.group_name,
            "author_guess": base.author_guess,
            "core_title_guess": base.core_title_guess,
            "title_aliases": base.title_aliases,
            "chapter_name": base.chapter_name,
            "chapter_index": base.chapter_index,
            "chapter_index_end": base.chapter_index_end,
            "chapter_title": base.chapter_title,
            "subtitle": base.subtitle,
            "tags": base.tags,
            "confidence": base.confidence,
            "needs_review": base.needs_review,
        },
    }
    return (
        "你是漫画论坛帖子标题结构化助手。"
        "你的任务是从帖子标题中提取：作者、汉化组、漫画名（系列名）、章节信息、其他标签。\n\n"

        "## 提取规则\n\n"

        "### 作者 author_guess\n"
        "方括号[]或［］内的内容为作者。如 [ポテトルス] → ポテトルス\n\n"

        "### 汉化组 group_name\n"
        "【】内含'汉化''組''组'等关键词的为汉化组。如 【提灯喵汉化组】 → 提灯喵汉化组\n\n"

        "### 漫画名 core_title_guess\n"
        "去除汉化组、作者、章节信息后的核心标题。\n\n"

        "### 章节信息\n\n"

        "**chapter_name（章节号+章节名）**：\n"
        "- 有'第N话/话N/第N章'等表达时，chapter_name 为完整表达。\n"
        "  例：'13话其2' → chapter_name='13话其2'\n"
        "  例：'第08话' → chapter_name='第08话'\n"
        "  例：'233话' → chapter_name='233话'\n"
        "- 裸数字无单位词（话/話/回/章）不算章节号。\n"
        "  例：'摇曳百合 233 两个人的活动记录' → 233 不是章节号，chapter_name='1'\n"
        "- 数字后跟冒号（N:标题）时，数字为章节号，冒号后为章节标题。\n"
        "  例：'33:露露娜大人、获得新天地' → chapter_name='33', chapter_title='露露娜大人、获得新天地'\n"
        "- 无任何章节信息 → chapter_name='1'\n"
        "- 特典/彩页/番外 → chapter_name 前加'999 '。例：'特典' → '999 特典'\n\n"

        "**chapter_index（章节数）**：\n"
        "- 从 chapter_name 提取数字。后缀映射：上/前篇=+0.1，下/后篇=+0.2\n"
        "  例：'13话其2' → 13.2\n"
        "  例：'第08话' → 8.0\n"
        "  例：'13话上' → 13.1\n"
        "  例：'13话前篇' → 13.1\n"
        "  例：'13话后篇' → 13.2\n"
        "- 范围章节（如 '51~60'）：chapter_index=51, chapter_index_end=60\n"
        "- 特典/彩页/番外 → 999\n"
        "- 无章节信息 → 1\n\n"

        "**chapter_title（章节名/副标题）**：\n"
        "- 章节号后的描述性文字。\n"
        "  例：'13话其2 两个人的活动记录' → chapter_title='两个人的活动记录'\n"
        "  例：'33:露露娜大人、获得新天地' → chapter_title='露露娜大人、获得新天地'\n"
        "- 没有则为 null\n\n"

        "### 其他\n"
        "- subtitle：书名号「」内的内容\n"
        "- tags：特殊标记如【!】\n\n"

        "请返回严格 JSON，不要解释，不要 markdown。"
        "JSON 字段：group_name, author_guess, core_title_guess, title_aliases, "
        "chapter_name, chapter_index, chapter_index_end, chapter_title, subtitle, tags, confidence, needs_review。\n"
        f"\n可用提示：\n{json.dumps(hints, ensure_ascii=False, indent=2)}"
    )


def _merge_title_payload(*, raw_title: str, base: TitleParseResult, payload: dict[str, object]) -> TitleParseResult:
    core_title = normalize_display_title(str(payload.get("core_title_guess") or base.core_title_guess or "").strip())
    series_key = core_title
    aliases = [
        normalize_display_title(str(item).strip())
        for item in (payload.get("title_aliases") or base.title_aliases or [])
        if str(item).strip()
    ]
    chapter_index = payload.get("chapter_index")
    try:
        chapter_value = float(chapter_index) if chapter_index not in {None, ""} else base.chapter_index
    except (TypeError, ValueError):
        chapter_value = base.chapter_index
    chapter_index_end = payload.get("chapter_index_end")
    try:
        chapter_end_value = float(chapter_index_end) if chapter_index_end not in {None, ""} else base.chapter_index_end
    except (TypeError, ValueError):
        chapter_end_value = base.chapter_index_end
    confidence = payload.get("confidence")
    try:
        confidence_value = float(confidence) if confidence not in {None, ""} else max(base.confidence, 0.8)
    except (TypeError, ValueError):
        confidence_value = max(base.confidence, 0.8)
    needs_review = payload.get("needs_review")
    if isinstance(needs_review, bool):
        needs_review_value = needs_review
    else:
        needs_review_value = False if core_title and series_key else base.needs_review
    result = TitleParseResult(
        display_title=base.display_title,
        group_name=_as_optional_text(payload.get("group_name"), base.group_name),
        author_guess=_as_optional_text(payload.get("author_guess"), base.author_guess),
        core_title_guess=core_title or base.core_title_guess,
        normalized_core_title=core_title or base.normalized_core_title,
        series_key=series_key or base.series_key,
        title_aliases=aliases,
        chapter_name=_as_optional_text(payload.get("chapter_name"), base.chapter_name),
        chapter_index=chapter_value,
        chapter_index_end=chapter_end_value,
        chapter_title=_as_optional_text(payload.get("chapter_title"), base.chapter_title),
        subtitle=_as_optional_text(payload.get("subtitle"), base.subtitle),
        tags=_as_text_list(payload.get("tags"), base.tags),
        confidence=confidence_value,
        needs_review=needs_review_value,
    )
    if result.chapter_name is None and result.chapter_index is not None:
        from dataclasses import replace
        result = replace(result, chapter_name=str(int(result.chapter_index)))
    return result


def title_parse_to_dict(parsed: TitleParseResult) -> dict[str, Any]:
    return {
        "display_title": parsed.display_title,
        "group_name": parsed.group_name,
        "author_guess": parsed.author_guess,
        "core_title_guess": parsed.core_title_guess,
        "normalized_core_title": parsed.normalized_core_title,
        "series_key": parsed.series_key,
        "title_aliases": list(parsed.title_aliases),
        "chapter_name": parsed.chapter_name,
        "chapter_index": parsed.chapter_index,
        "chapter_index_end": parsed.chapter_index_end,
        "chapter_title": parsed.chapter_title,
        "subtitle": parsed.subtitle,
        "tags": list(parsed.tags),
        "confidence": parsed.confidence,
        "needs_review": parsed.needs_review,
    }


def _as_optional_text(value: object, fallback: str | None) -> str | None:
    if value is None:
        return fallback
    text = normalize_display_title(str(value).strip())
    return text or fallback


def _as_text_list(value: object, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    items = [normalize_display_title(str(item).strip()) for item in value if str(item).strip()]
    return items or fallback


__all__ = [
    "LLMRequestError",
    "refine_title_parse_with_llm",
    "should_refine_title_with_llm",
    "title_parse_to_dict",
]
