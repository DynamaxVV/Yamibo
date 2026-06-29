from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class PageType(StrEnum):
    THREAD_DETAIL = "thread_detail"
    FORUM_LIST = "forum_list"
    SEARCH_RESULT = "search_result"
    LOGIN_REQUIRED = "login_required"
    REMOTE_MAINTENANCE = "remote_maintenance"
    PROMPT_FORUM_CLOSED = "prompt_forum_closed"
    PROMPT_THREAD_MISSING_OR_REMOVED_OR_REVIEW = "prompt_thread_missing_or_removed_or_review"
    PROMPT_THREAD_PERMISSION_REQUIRED = "prompt_thread_permission_required"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PageClassification:
    page_type: PageType
    reason: str


def classify_html(html: str) -> PageClassification:
    text = html.lower()
    # 先识别最危险的页面类型，避免后续把登录页或维护页误判成正常帖子。
    if "百合会每日维护" in html or "alt=\"每日维护\"" in html:
        return PageClassification(PageType.REMOTE_MAINTENANCE, "maintenance marker")
    if (
        "您尚未登录" in html
        or "没有权限访问该版块" in html
        or "用户登录" in html
        or "<title>登录" in html
        or 'class="pg_logging"' in text
    ):
        return PageClassification(PageType.LOGIN_REQUIRED, "login required marker")
    if 'id="thread_subject"' in text or "id='thread_subject'" in text:
        return PageClassification(PageType.THREAD_DETAIL, "thread subject marker")
    if 'id="threadlisttableid"' in text or "normalthread_" in text or 'class="s xst"' in text:
        return PageClassification(PageType.FORUM_LIST, "forum list marker")
    if 'class="pbw"' in text and 'class="xs3"' in text:
        return PageClassification(PageType.SEARCH_RESULT, "search result marker")
    if "<title>搜索" in html:
        return PageClassification(PageType.SEARCH_RESULT, "search title")
    prompt_text = _extract_discuz_prompt_text(html)
    if prompt_text:
        prompt_text_lower = prompt_text.lower()
        if any(marker in prompt_text for marker in ("查无此区", "此区已关闭", "版块已关闭")):
            return PageClassification(PageType.PROMPT_FORUM_CLOSED, f"discuz prompt: {prompt_text}")
        if any(marker in prompt_text for marker in ("指定的主题不存在", "已被删除", "正在被审核")):
            return PageClassification(
                PageType.PROMPT_THREAD_MISSING_OR_REMOVED_OR_REVIEW,
                f"discuz prompt: {prompt_text}",
            )
        if re.search(r"阅读权限高于\s*\d+", prompt_text):
            return PageClassification(PageType.PROMPT_THREAD_PERMISSION_REQUIRED, f"discuz prompt: {prompt_text}")
        if "提示信息" in html and prompt_text_lower:
            return PageClassification(PageType.UNKNOWN, f"discuz prompt: {prompt_text}")
    return PageClassification(PageType.UNKNOWN, "no known marker")


def _extract_discuz_prompt_text(html: str) -> str | None:
    prompt_match = re.search(
        r'<div[^>]+id=["\']messagetext["\'][^>]*>(.*?)</div>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if prompt_match is None:
        prompt_match = re.search(
            r'<div[^>]+class=["\'][^"\']*\balert_(?:error|info)\b[^"\']*["\'][^>]*>(.*?)</div>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if prompt_match is None:
        return None
    body = prompt_match.group(1)
    first_paragraph = re.search(r"<p[^>]*>(.*?)</p>", body, flags=re.IGNORECASE | re.DOTALL)
    if first_paragraph is not None:
        body = first_paragraph.group(1)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
