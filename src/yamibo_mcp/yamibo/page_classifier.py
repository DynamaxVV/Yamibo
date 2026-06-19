from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PageType(StrEnum):
    THREAD_DETAIL = "thread_detail"
    FORUM_LIST = "forum_list"
    SEARCH_RESULT = "search_result"
    LOGIN_REQUIRED = "login_required"
    REMOTE_MAINTENANCE = "remote_maintenance"
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
    return PageClassification(PageType.UNKNOWN, "no known marker")
