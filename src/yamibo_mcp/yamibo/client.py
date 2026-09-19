from __future__ import annotations

import gzip
import html as html_lib
import logging
import random
import re
import time
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

import curl_cffi.requests as curl_requests
from curl_cffi import CurlHttpVersion
from curl_cffi.requests import BrowserType

from yamibo_mcp.errors import (
    LoginRequiredError,
    RemoteFetchError,
    RemoteMaintenanceError,
    ThreadPermissionRequiredError,
    UnexpectedPageError,
)
from yamibo_mcp.structured_logging import emit
from yamibo_mcp.yamibo.anti_bot import is_soft_block_page
from yamibo_mcp.yamibo.cf_challenge import solve_acw_sc__v2_if_present
from yamibo_mcp.yamibo.waf_challenge import (
    WafChallengeError,
    solve_nox_challenge_if_present,
)
from yamibo_mcp.yamibo.parsers.forum_list import ForumThreadItem, extract_total_pages, parse_forum_list
from yamibo_mcp.yamibo.parsers.thread_detail import extract_author_only_total_pages
from yamibo_mcp.yamibo.parsers.search_results import SearchResultItem, parse_search_results
from yamibo_mcp.yamibo.page_classifier import PageType, classify_html
from yamibo_mcp.yamibo.runtime_limits import throttle_cookie_request
from yamibo_mcp.yamibo.urls import (
    DEFAULT_FORUM_ID,
    dateline_forum_page_url,
    forum_page_url,
    is_yamibo_site_content_image_url,
    normalize_forum_page_url,
    normalize_thread_url,
    thread_page_url_from_tid,
    thread_url_from_tid,
    stable_attachment_id,
)

# ── Browser-like UA pool ──────────────────────────────────────────────────

# Keep the visible browser identity aligned with curl_cffi's
# BrowserType.chrome131 TLS fingerprint and sec-ch-ua-platform="macOS".
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_SEC_CH_UA = '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"'

DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    "Cache-Control": "max-age=0",
    "sec-ch-ua": _SEC_CH_UA,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_COOKIE_FILE_LOCKS: dict[Path, threading.Lock] = {}
_COOKIE_FILE_LOCKS_GUARD = threading.Lock()


def _cookie_file_lock(path: Path) -> threading.Lock:
    normalized = path.expanduser().resolve()
    with _COOKIE_FILE_LOCKS_GUARD:
        return _COOKIE_FILE_LOCKS.setdefault(normalized, threading.Lock())


def _parse_cookie_text(raw: str) -> dict[str, str]:
    stripped = raw.strip()
    if not stripped:
        return {}
    if ";" in stripped and "\n" not in stripped:
        pairs = [segment.strip() for segment in stripped.split(";") if "=" in segment]
    else:
        pairs = [line.strip() for line in stripped.splitlines() if line.strip() and not line.strip().startswith("#")]
    cookies: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        if name.strip():
            cookies[name.strip()] = value.strip()
    return cookies

# ── Natural burst timing ──────────────────────────────────────────────────

_BURST_LIMIT = (1, 4)
_BURST_INTERVAL = (0.3, 1.5)
_PAUSE_INTERVAL = (2.0, 8.0)


class BurstThrottle:
    """Natural-feeling request timing — short bursts separated by pauses."""

    def __init__(self) -> None:
        self._count = 0
        self._limit = random.randint(*_BURST_LIMIT)

    def wait(self) -> None:
        self._count += 1
        if self._count > self._limit:
            time.sleep(random.uniform(*_PAUSE_INTERVAL))
            self._count = 0
            self._limit = random.randint(*_BURST_LIMIT)
        else:
            time.sleep(random.uniform(*_BURST_INTERVAL))


LOG = logging.getLogger(__name__)

_CHALLENGE_COOKIE_NAMES = ("nox_jst_v1", "acw_sc__v2")


def _is_soft_block_fetch_error(exc: Exception) -> bool:
    if not isinstance(exc, RemoteFetchError):
        return False
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("soft block", "cf challenge", "captcha", "waf challenge")
    )


def _random_ua() -> str:
    # Stable UA keeps persisted WAF cookies bound to one coherent browser identity.
    return _BROWSER_USER_AGENT


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    html: str


@dataclass(frozen=True)
class ImageFetchResponse:
    """Binary response returned by the authenticated forum session."""
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes


def daily_checkin_already_done(html: str) -> bool:
    """Return whether the sign-in page says this account already checked in today."""
    page = html_lib.unescape(html)
    for match in re.finditer(
        r'<a\b[^>]*href=["\'][^"\']*plugin\.php\?id=zqlj_sign(?:&[^"\']*)?["\'][^>]*>(.*?)</a>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        link_text = re.sub(r"<[^>]+>", " ", match.group(1))
        link_text = re.sub(r"\s+", " ", link_text).strip()
        if "今日已打卡" in link_text:
            return True
    return False


def daily_checkin_action_url(html: str, *, base_url: str, fallback_url: str) -> str:
    """Resolve the current page's check-in action, whose formhash may change."""
    page = html_lib.unescape(html)
    candidates: list[tuple[str, str]] = []
    for match in re.finditer(
        r'<a\b[^>]*href=["\']([^"\']*plugin\.php\?id=zqlj_sign&[^"\']*)["\'][^>]*>(.*?)</a>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = match.group(1)
        link_text = re.sub(r"<[^>]+>", " ", match.group(2))
        link_text = re.sub(r"\s+", " ", link_text).strip()
        candidates.append((href, link_text))
        if "点击打卡" in link_text:
            return urllib.parse.urljoin(base_url, href)
    if candidates:
        return urllib.parse.urljoin(base_url, candidates[0][0])
    return fallback_url


def parse_daily_checkin_profile(html: str) -> dict[str, str | int] | None:
    """Extract the current account's summary from the sign-in page's "我的记录" block."""
    page = html_lib.unescape(html)
    page = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", page, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", page)
    text = re.sub(r"\s+", " ", text).strip()

    def value_between(label: str, next_labels: tuple[str, ...]) -> str | None:
        boundary = "|".join(re.escape(item) for item in next_labels)
        match = re.search(rf"{re.escape(label)}\s*[：:]\s*(.+?)(?=\s+(?:{boundary})(?:\s*[：:])?|$)", text)
        return match.group(1).strip() if match else None

    recent = value_between("最近打卡", ("本月打卡",))
    month = value_between("本月打卡", ("连续打卡",))
    consecutive = value_between("连续打卡", ("累计打卡",))
    total = value_between("累计打卡", ("累计奖励",))
    level = value_between("当前打卡等级", ("打卡统计", "打卡等级"))
    if not all((recent, month, consecutive, total, level)):
        return None

    def days(value: str) -> int:
        match = re.search(r"\d+", value)
        if not match:
            raise ValueError(f"invalid sign-in day count: {value}")
        return int(match.group(0))

    return {
        "recent_checkin": recent,
        "month_days": days(month),
        "consecutive_days": days(consecutive),
        "total_days": days(total),
        "level": level,
        "today_status": "checked" if daily_checkin_already_done(html) else "not_checked",
    }


def validate_daily_checkin_result(result: FetchResult) -> FetchResult:
    """Require an authenticated page and an explicit check-in success signal."""
    if not 200 <= result.status_code < 400:
        raise RemoteFetchError(
            f"daily sign-in request returned HTTP {result.status_code} for {result.final_url}",
            details={"url": result.final_url, "status_code": result.status_code, "retryable": True},
        )
    if is_soft_block_page(result.html):
        raise RemoteFetchError(f"soft block detected for {result.final_url}")
    if classify_html(result.html).page_type == PageType.LOGIN_REQUIRED:
        raise LoginRequiredError(f"login required for {result.final_url}")
    # Discuz exposes both markers on authenticated pages. A 200 response alone
    # is insufficient because the WAF can return a normal-looking HTML page.
    authenticated = re.search(r"discuz_uid\s*=\s*['\"]([1-9]\d*)", result.html)
    authenticated = authenticated or re.search(r"action=(?:logout|logging%26action%3Dlogout)", result.html)
    if not authenticated:
        raise LoginRequiredError(f"daily sign-in page has no authenticated session: {result.final_url}")
    page = html_lib.unescape(result.html)
    if "恭喜您，打卡成功" not in page and not daily_checkin_already_done(result.html):
        raise RemoteFetchError(
            f"daily sign-in success not confirmed for {result.final_url}",
            details={"url": result.final_url, "status_code": result.status_code, "retryable": False},
        )
    return result


class YamiboClient:
    SIGN_IN_PAGE_URL = "https://bbs.yamibo.com/plugin.php?id=zqlj_sign"
    SIGN_IN_ACTION_URL = "https://bbs.yamibo.com/plugin.php?id=zqlj_sign&sign=35981a55"
    def __init__(
        self,
        *,
        timeout: float = 15.0,
        retries: int = 2,
        headers: dict[str, str] | None = None,
        cookie_jar: CookieJar | None = None,
        cookie_file: str | None = None,
        persist_cookies: bool = False,
        use_system_proxy: bool = False,
        proxy_url: str | None = None,
        login_username: str | None = None,
        login_password: str | None = None,
        request_interval: float = 1.0,
        request_interval_jitter: float = 0.5,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.cookie_file = Path(cookie_file).expanduser() if cookie_file else None
        self.persist_cookies = persist_cookies or self.cookie_file is not None
        self.use_system_proxy = use_system_proxy
        self.proxy_url = proxy_url
        self.login_username = login_username
        self.login_password = login_password
        self._request_interval = request_interval
        self._request_interval_jitter = request_interval_jitter
        self._burst = BurstThrottle()
        # curl_cffi sessions share connection/cookie state and are not safe to
        # use concurrently from the image download pool.
        self._session_lock = threading.RLock()

        # Build public headers dict for external consumers (image downloader etc.)
        resolved: dict[str, str] = dict(DEFAULT_HEADERS)
        resolved["User-Agent"] = _random_ua()
        if headers:
            resolved.update(headers)
        self.headers = resolved

        # Proxy setup
        proxies: dict[str, str] | None = None
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}
        elif not use_system_proxy:
            proxies = {"http": "", "https": ""}

        self._http_version = CurlHttpVersion.V2_0  # 默认 HTTP/2，遇到 PROTOCOL_ERROR 时降级

        # curl_cffi Session — HTTP/2, browser TLS fingerprint
        self._session = curl_requests.Session(
            impersonate=BrowserType.chrome131,
            http_version=self._http_version,
            timeout=timeout,
            proxies=proxies,
        )

        self._load_cookies()
        self._bootstrap_login_if_needed()

    # ── Backward-compat properties ───────────────────────────────────────

    @property
    def cookie_jar(self) -> CookieJar:
        jar = CookieJar()
        for name, value in self._session.cookies.items():
            jar.set_cookie(Cookie(
                version=0, name=name, value=value,
                port=None, port_specified=False,
                domain="bbs.yamibo.com", domain_specified=True,
                domain_initial_dot=False, path="/", path_specified=True,
                secure=False, expires=None, discard=True,
                comment=None, comment_url=None, rest={}, rfc2109=False,
            ))
        return jar

    # ── Headers ──────────────────────────────────────────────────────────

    def _request_headers(self, *, referer: str | None = None) -> dict[str, str]:
        h = dict(self.headers)
        # Keep the browser fingerprint stable for the lifetime of a session.
        # Baidu WAF binds nox_jst_v1 to the User-Agent that received the challenge.
        h["User-Agent"] = self.headers["User-Agent"]
        if referer:
            h["Referer"] = referer
            h["Sec-Fetch-Site"] = "same-origin"
        return h

    def _image_request_headers(self, *, referer: str | None = None) -> dict[str, str]:
        headers = self._request_headers(referer=referer)
        headers.update({
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
        })
        headers.pop("Sec-Fetch-User", None)
        headers.pop("Upgrade-Insecure-Requests", None)
        return headers

    # ── Public fetch API ─────────────────────────────────────────────────

    def fetch_url(self, url: str, *, referer: str | None = None) -> FetchResult:
        return self._fetch_with_validation(url, self._validate_thread_page, referer=referer)

    def fetch_image(
        self,
        url: str,
        *,
        referer: str | None = None,
        timeout: float | None = None,
    ) -> ImageFetchResponse:
        """Fetch an image through this client's current session.

        This deliberately returns a small immutable response instead of the
        curl_cffi response object so worker threads cannot retain mutable
        session state.  A soft-block/challenge page is first opened through
        the normal WAF path, then the binary request is retried with the
        resulting cookies.
        """
        request_headers = self._image_request_headers(referer=referer)
        request_timeout = self.timeout if timeout is None else timeout
        with self._session_lock:
            try:
                response = self._session.get(url, headers=request_headers, timeout=request_timeout)
            except (TimeoutError, curl_requests.errors.RequestsError) as exc:
                if self._is_protocol_error(exc):
                    self._reset_session()
                raise
            body = bytes(response.content)
            if is_yamibo_site_content_image_url(url) and is_soft_block_page(
                body.decode("utf-8", errors="ignore")
            ):
                # _open_html solves nox/acw challenges and persists cookies.
                self._open_html(url, referer=referer)
                request_headers = self._image_request_headers(referer=referer)
                response = self._session.get(url, headers=request_headers, timeout=request_timeout)
                body = bytes(response.content)
            self._save_cookies()
            return ImageFetchResponse(
                url=url,
                final_url=str(response.url),
                status_code=int(response.status_code),
                headers={str(k): str(v) for k, v in response.headers.items()},
                content=body,
            )

    def sign_daily_checkin(
        self,
        *,
        page_url: str = SIGN_IN_PAGE_URL,
        action_url: str = SIGN_IN_ACTION_URL,
    ) -> FetchResult:
        """Open the sign-in page, then follow its configured check-in action."""
        page = self.fetch_daily_checkin_page(page_url=page_url)
        if daily_checkin_already_done(page.html):
            self._save_cookies()
            return page
        resolved_action_url = daily_checkin_action_url(
            page.html,
            base_url=page.final_url,
            fallback_url=action_url,
        )
        result = self._open_authenticated_page(resolved_action_url, referer=page.final_url)
        self._save_cookies()
        return validate_daily_checkin_result(result)

    def fetch_daily_checkin_page(self, *, page_url: str = SIGN_IN_PAGE_URL) -> FetchResult:
        """Fetch the authenticated sign-in page without following the check-in action."""
        return self._open_authenticated_page(page_url, referer="https://bbs.yamibo.com/")

    def _open_authenticated_page(self, url: str, *, referer: str | None = None) -> FetchResult:
        self._throttle()
        result = self._open_html_with_retry(url, referer=referer)
        classification = classify_html(result.html)
        if classification.page_type == PageType.LOGIN_REQUIRED:
            if not self._can_login():
                raise LoginRequiredError(f"login required for {result.final_url}")
            self._login(base_url=self._base_url_for(url), referer=referer)
            self._throttle()
            result = self._open_html_with_retry(url, referer=referer)
            classification = classify_html(result.html)
        if is_soft_block_page(result.html):
            raise RemoteFetchError(
                f"soft block detected for {result.final_url}",
                details={"url": result.final_url, "status_code": result.status_code, "retryable": False},
            )
        if classification.page_type == PageType.LOGIN_REQUIRED:
            raise LoginRequiredError(f"login required for {result.final_url}")
        if classification.page_type == PageType.REMOTE_MAINTENANCE:
            raise RemoteMaintenanceError(f"remote maintenance for {result.final_url}")
        if not 200 <= result.status_code < 400:
            raise RemoteFetchError(
                f"sign-in request returned HTTP {result.status_code} for {result.final_url}",
                details={"url": result.final_url, "status_code": result.status_code, "retryable": True},
            )
        return result

    def fetch_thread_by_tid(self, tid: int, *, base_url: str | None = None) -> FetchResult:
        resolved_base = base_url or "https://bbs.yamibo.com"
        return self.fetch_url(thread_url_from_tid(tid, base_url=resolved_base), referer=f"{resolved_base}/")

    def fetch_thread(self, *, tid: int | None = None, url: str | None = None,
                     base_url: str | None = None) -> FetchResult:
        resolved_base = base_url or "https://bbs.yamibo.com"
        if url:
            normalized = normalize_thread_url(url, base_url=resolved_base)
            return self.fetch_url(normalized, referer=f"{resolved_base}/")
        if tid is None:
            raise ValueError("fetch_thread requires tid or url")
        return self.fetch_thread_by_tid(tid, base_url=resolved_base)

    def fetch_thread_page(self, *, tid: int, page: int, author_uid: str | None = None,
                          base_url: str | None = None) -> FetchResult:
        resolved_base = base_url or "https://bbs.yamibo.com"
        return self.fetch_url(
            thread_page_url_from_tid(tid, page=page, author_uid=author_uid, base_url=resolved_base),
            referer=thread_page_url_from_tid(tid, page=1, base_url=resolved_base),
        )

    def fetch_author_only_thread_pages(
        self, *, tid: int, author_uid: str, base_url: str | None = None,
        max_pages: int, page_delay_seconds: float,
        before_each_page: Callable[[], None] | None = None,
    ) -> tuple[list[FetchResult], int | None, str]:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if page_delay_seconds < 0:
            raise ValueError("page_delay_seconds must be non-negative")
        first_page = self.fetch_thread_page(tid=tid, page=1, author_uid=author_uid, base_url=base_url)
        total_pages = extract_author_only_total_pages(first_page.html, tid=tid, author_uid=author_uid)
        results = [first_page]
        max_target_page = max_pages if total_pages is None else min(total_pages, max_pages)
        for page in range(2, max_target_page + 1):
            if before_each_page is not None:
                before_each_page()
            if page_delay_seconds > 0:
                time.sleep(page_delay_seconds)
            results.append(self.fetch_thread_page(tid=tid, page=page, author_uid=author_uid, base_url=base_url))
        stopped_reason = "max_pages" if (total_pages or 0) > max_pages else "last_page"
        if total_pages is None and len(results) >= max_pages:
            stopped_reason = "max_pages"
        return results, total_pages, stopped_reason

    def fetch_thread_pages(
        self, *, tid: int, base_url: str | None = None, max_pages: int,
        first_page: FetchResult | None = None, page_delay_seconds: float = 0.0,
        before_each_page: Callable[[], None] | None = None,
    ) -> tuple[list[FetchResult], int, str]:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        if page_delay_seconds < 0:
            raise ValueError("page_delay_seconds must be non-negative")
        first_result = first_page or self.fetch_thread_page(tid=tid, page=1, base_url=base_url)
        total_pages = max(extract_total_pages(first_result.html), 1)
        results = [first_result]
        max_target_page = min(total_pages, max_pages)
        for page in range(2, max_target_page + 1):
            if before_each_page is not None:
                before_each_page()
            if page_delay_seconds > 0:
                time.sleep(page_delay_seconds)
            results.append(self.fetch_thread_page(tid=tid, page=page, base_url=base_url))
        stopped_reason = "max_pages" if total_pages > max_pages else "last_page"
        return results, total_pages, stopped_reason

    def fetch_forum_page(self, *, page: int | None = None, url: str | None = None,
                         base_url: str | None = None, forum_id: int = DEFAULT_FORUM_ID) -> FetchResult:
        resolved_base = base_url or "https://bbs.yamibo.com"
        if url:
            normalized = normalize_forum_page_url(url, base_url=resolved_base)
        else:
            if page is None:
                raise ValueError("fetch_forum_page requires page or url")
            normalized = forum_page_url(page, forum_id=forum_id, base_url=resolved_base)
        return self.fetch_url_allowing_forum_list(normalized, referer=f"{resolved_base}/")

    def fetch_forum_threads(self, *, page: int | None = None, url: str | None = None,
                            base_url: str | None = None, forum_id: int = DEFAULT_FORUM_ID,
                            ) -> tuple[FetchResult, list[ForumThreadItem]]:
        result = self.fetch_forum_page(page=page, url=url, base_url=base_url, forum_id=forum_id)
        return result, parse_forum_list(result.html)

    def fetch_forum_threads_dateline(self, *, page: int, base_url: str | None = None,
                                     forum_id: int = DEFAULT_FORUM_ID,
                                     ) -> tuple[FetchResult, list[ForumThreadItem], int]:
        resolved_base = base_url or "https://bbs.yamibo.com"
        url = dateline_forum_page_url(page, forum_id=forum_id, base_url=resolved_base)
        result = self.fetch_url_allowing_forum_list(url, referer=f"{resolved_base}/")
        return result, parse_forum_list(result.html), extract_total_pages(result.html)

    def fetch_search_results(
        self, *, query: str, base_url: str | None = None, forum_id: int = DEFAULT_FORUM_ID,
    ) -> tuple[FetchResult, list[SearchResultItem]]:
        resolved_base = (base_url or "https://bbs.yamibo.com").rstrip("/")
        forum_result = self.fetch_forum_page(page=1, base_url=resolved_base)
        formhash = _extract_formhash(forum_result.html)
        payload = urllib.parse.urlencode({
            "mod": "curforum", "formhash": formhash, "srchtype": "title",
            "srhfid": str(forum_id), "srhlocality": "forum::forumdisplay",
            "srchtxt": query, "searchsubmit": "true",
        }).encode("utf-8")
        search_url = f"{resolved_base}/search.php?searchsubmit=yes"
        headers = {
            **self._request_headers(referer=forum_result.final_url),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = self._session.post(search_url, data=payload, headers=headers, timeout=self.timeout)
        html_text = _decode_response_body(resp.content, resp.headers.get("Content-Encoding", ""))
        result = FetchResult(url=search_url, final_url=resp.url, status_code=resp.status_code, html=html_text)
        self._validate_search_page(result)
        self._save_cookies()
        return result, parse_search_results(result.html)

    def fetch_search_results_all(
        self, *, query: str, base_url: str | None = None, forum_id: int = DEFAULT_FORUM_ID,
        start_page: int = 1, end_page: int | None = None,
    ) -> tuple[list[SearchResultItem], list[str], int]:
        if start_page <= 0:
            raise ValueError("start_page must be positive")
        if end_page is not None and end_page < start_page:
            raise ValueError("end_page must be greater than or equal to start_page")
        first_result, first_items = self.fetch_search_results(query=query, base_url=base_url, forum_id=forum_id)
        resolved_base = base_url or self._base_url_for(first_result.final_url)
        search_urls = _extract_search_page_urls(first_result.html, base_url=resolved_base)
        total_pages = max(search_urls) if search_urls else 1
        final_end_page = total_pages if end_page is None else min(end_page, total_pages)
        seen_tids: set[int] = set()
        collected: list[SearchResultItem] = []
        scanned_pages: list[str] = []

        def add_items(items: list[SearchResultItem]) -> None:
            for item in items:
                if item.tid in seen_tids:
                    continue
                seen_tids.add(item.tid)
                collected.append(item)

        if start_page == 1:
            scanned_pages.append(first_result.final_url)
            add_items(first_items)
        for page in range(max(start_page, 2), final_end_page + 1):
            page_url = search_urls.get(page) or _search_page_url_from_first_result(first_result.final_url, page=page)
            result = self.fetch_url_allowing_search(page_url, referer=first_result.final_url)
            scanned_pages.append(result.final_url)
            add_items(parse_search_results(result.html))
        return collected, scanned_pages, total_pages

    # ── Validators ───────────────────────────────────────────────────────

    def _validate_thread_page(self, result: FetchResult) -> None:
        if is_soft_block_page(result.html):
            emit(LOG, logging.WARNING, "remote.soft_block",
                 f"Soft block detected for {result.final_url}",
                 result="failure", status="blocked",
                 error_code="REMOTE_SOFT_BLOCK", tags=["remote", "anti_bot"])
            raise RemoteFetchError(
                f"soft block (CF challenge / CAPTCHA) detected for {result.final_url}",
                details={"url": result.final_url, "status_code": result.status_code, "retryable": False},
            )
        classification = classify_html(result.html)
        if classification.page_type == PageType.LOGIN_REQUIRED:
            emit(LOG, logging.WARNING, "remote.login_required",
                 f"Login required for {result.final_url}",
                 result="failure", status="unauthenticated",
                 error_code="REMOTE_LOGIN_REQUIRED", tags=["remote", "auth"])
            raise LoginRequiredError(f"login required for {result.final_url}")
        if classification.page_type == PageType.REMOTE_MAINTENANCE:
            emit(LOG, logging.WARNING, "remote.maintenance",
                 f"Remote maintenance for {result.final_url}",
                 result="failure", status="unavailable",
                 error_code="REMOTE_MAINTENANCE", tags=["remote"])
            raise RemoteMaintenanceError(f"remote maintenance for {result.final_url}")
        if classification.page_type == PageType.PROMPT_THREAD_PERMISSION_REQUIRED:
            prompt_text = _extract_discuz_prompt_text(result.html)
            required_permission = _extract_required_read_permission(prompt_text)
            raise ThreadPermissionRequiredError(
                f"thread requires read permission above {required_permission if required_permission is not None else 'unknown'} for {result.final_url}",
                required_permission=required_permission,
                details={"url": result.final_url, "page_type": classification.page_type.value, "prompt_text": prompt_text},
            )
        if classification.page_type == PageType.PROMPT_USER_GROUP_UPGRADE_REQUIRED:
            prompt_text = _extract_discuz_prompt_text(result.html)
            raise ThreadPermissionRequiredError(
                f"user group upgrade required for {result.final_url}: {prompt_text}",
                details={"url": result.final_url, "page_type": classification.page_type.value, "prompt_text": prompt_text},
            )
        if classification.page_type != PageType.THREAD_DETAIL:
            prompt_text = _extract_discuz_prompt_text(result.html)
            if prompt_text:
                raise UnexpectedPageError(
                    f"expected thread detail page but got prompt page for {result.final_url}: {prompt_text}",
                    details={"url": result.final_url, "page_type": classification.page_type.value, "prompt_text": prompt_text},
                )
            # 未知页面类型 —— 携带 HTML 摘要用于诊断，可能是反爬页面
            html_snippet = result.html[:500] if len(result.html) > 500 else result.html
            page_title = _extract_html_title(result.html)
            raise UnexpectedPageError(
                f"expected thread detail page but got {classification.page_type.value} for {result.final_url}",
                details={
                    "url": result.final_url,
                    "page_type": classification.page_type.value,
                    "reason": classification.reason,
                    "html_length": len(result.html),
                    "html_snippet": html_snippet,
                    "page_title": page_title,
                },
            )

    def fetch_url_allowing_forum_list(self, url: str, *, referer: str | None = None) -> FetchResult:
        return self._fetch_with_validation(url, self._validate_forum_page, referer=referer)

    def fetch_url_allowing_search(self, url: str, *, referer: str | None = None) -> FetchResult:
        return self._fetch_with_validation(url, self._validate_search_page, referer=referer)

    def _validate_forum_page(self, result: FetchResult) -> None:
        if is_soft_block_page(result.html):
            emit(LOG, logging.WARNING, "remote.soft_block",
                 f"Soft block detected for {result.final_url}",
                 result="failure", status="blocked",
                 error_code="REMOTE_SOFT_BLOCK", tags=["remote", "anti_bot"])
            raise RemoteFetchError(
                f"soft block detected for {result.final_url}",
                details={"url": result.final_url, "status_code": result.status_code, "retryable": False},
            )
        classification = classify_html(result.html)
        if classification.page_type == PageType.LOGIN_REQUIRED:
            raise LoginRequiredError(f"login required for {result.final_url}")
        if classification.page_type == PageType.REMOTE_MAINTENANCE:
            raise RemoteMaintenanceError(f"remote maintenance for {result.final_url}")
        if classification.page_type != PageType.FORUM_LIST:
            raise UnexpectedPageError(
                f"expected forum list page but got {classification.page_type.value} for {result.final_url}"
            )

    def _validate_search_page(self, result: FetchResult) -> None:
        if is_soft_block_page(result.html):
            emit(LOG, logging.WARNING, "remote.soft_block",
                 f"Soft block detected for {result.final_url}",
                 result="failure", status="blocked",
                 error_code="REMOTE_SOFT_BLOCK", tags=["remote", "anti_bot"])
            raise RemoteFetchError(
                f"soft block detected for {result.final_url}",
                details={"url": result.final_url, "status_code": result.status_code, "retryable": False},
            )
        classification = classify_html(result.html)
        if classification.page_type == PageType.LOGIN_REQUIRED:
            raise LoginRequiredError(f"login required for {result.final_url}")
        if classification.page_type == PageType.REMOTE_MAINTENANCE:
            raise RemoteMaintenanceError(f"remote maintenance for {result.final_url}")
        if classification.page_type != PageType.SEARCH_RESULT:
            raise UnexpectedPageError(
                f"expected search result page but got {classification.page_type.value} for {result.final_url}"
            )

    # ── Core fetch + retry ───────────────────────────────────────────────

    def _retry_delay(self, attempt: int) -> float:
        base = min(2.0 * (2.0 ** attempt), 60.0)
        return base * random.uniform(0.5, 1.5)

    def _throttle(self) -> None:
        throttle_cookie_request(
            self.cookie_file,
            request_interval=self._request_interval,
            request_interval_jitter=self._request_interval_jitter,
        )
        self._burst.wait()

    def _clear_challenge_cookies(self) -> None:
        """Drop only anti-bot challenge cookies before a bounded local retry."""
        for name in _CHALLENGE_COOKIE_NAMES:
            try:
                self._session.cookies.delete(name)
            except (KeyError, ValueError):
                pass

    def _reset_session(self) -> None:
        """重新创建 curl_cffi Session，用于 HTTP/2 协议错误后恢复。

        PROTOCOL_ERROR (curl 92) 等 HTTP/2 层错误会使当前连接损坏，
        后续请求复用同一 session 必然再次失败，必须重建 TCP 连接。
        遇到 PROTOCOL_ERROR 时自动降级到 HTTP/1.1 避免协议层问题。
        """
        # 遇到协议错误时降级到 HTTP/1.1，避免 HTTP/2 多路复用流问题反复出现
        if self._http_version == CurlHttpVersion.V2_0:
            self._http_version = CurlHttpVersion.V1_1
            LOG.info("Downgrading HTTP version from 2.0 to 1.1 after protocol error")
        proxies: dict[str, str] | None = None
        if self.proxy_url:
            proxies = {"http": self.proxy_url, "https": self.proxy_url}
        elif not self.use_system_proxy:
            proxies = {"http": "", "https": ""}
        self._session.close()
        self._session = curl_requests.Session(
            impersonate=BrowserType.chrome131,
            http_version=self._http_version,
            timeout=self.timeout,
            proxies=proxies,
        )
        self._load_cookies()

    @staticmethod
    def _is_protocol_error(exc: Exception) -> bool:
        """检测 HTTP/2 协议错误（curl error 92 PROTOCOL_ERROR）。"""
        msg = str(exc)
        return "PROTOCOL_ERROR" in msg or "HTTP/2 stream" in msg or "stream was not closed cleanly" in msg

    def _fetch_with_validation(self, url: str, validator, *, allow_login_retry: bool = True,
                                referer: str | None = None) -> FetchResult:
        self._throttle()
        last_error: Exception | None = None
        last_error_details: dict[str, object] = {}
        soft_block_recovery_used = False
        for attempt in range(self.retries + 1):
            try:
                result = self._open_html(url, referer=referer)
                validator(result)
                self._save_cookies()
                return result
            except LoginRequiredError as exc:
                last_error = exc
                if allow_login_retry and self._can_login():
                    try:
                        self._login(base_url=self._base_url_for(url), referer=url)
                        return self._fetch_with_validation(url, validator, allow_login_retry=False, referer=referer)
                    except (TimeoutError, curl_requests.errors.RequestsError) as login_exc:
                        last_error = login_exc
                        if attempt >= self.retries:
                            break
                        time.sleep(self._retry_delay(attempt))
                        continue
                break
            except RemoteFetchError as exc:
                if (
                    _is_soft_block_fetch_error(exc)
                    and not soft_block_recovery_used
                    and attempt < self.retries
                ):
                    soft_block_recovery_used = True
                    last_error = exc
                    LOG.info(
                        "Soft-block detected for %s; retrying once in the same session before proxy/account rotation",
                        url,
                    )
                    self._clear_challenge_cookies()
                    time.sleep(min(self._retry_delay(attempt), 2.0))
                    continue
                if _is_soft_block_fetch_error(exc):
                    details = dict(getattr(exc, "details", {}) or {})
                    details.update({
                        "session_recovery_attempted": soft_block_recovery_used,
                        "attempts": attempt + 1,
                    })
                    raise RemoteFetchError(str(exc), details=details) from exc
                raise
            except (TimeoutError, curl_requests.errors.RequestsError) as exc:
                last_error = exc
                last_error_details = {
                    "attempt": attempt + 1, "error_type": exc.__class__.__name__, "error_message": str(exc),
                }
                if attempt >= self.retries:
                    break
                if self._is_protocol_error(exc):
                    LOG.info(
                        "HTTP/2 protocol error detected, resetting session with http_version=%s before retry %d/%d: %s",
                        "1.1" if self._http_version == CurlHttpVersion.V1_1 else "2.0",
                        attempt + 1, self.retries, exc,
                    )
                    try:
                        self._reset_session()
                    except Exception:
                        LOG.warning("Failed to reset session after protocol error", exc_info=True)
                time.sleep(self._retry_delay(attempt))
        if isinstance(last_error, LoginRequiredError):
            raise last_error
        raise RemoteFetchError(
            f"failed to fetch {url} after {self.retries + 1} attempt(s) with timeout={self.timeout}s: {last_error}",
            details={
                "url": url, "attempts": self.retries + 1, "timeout_seconds": self.timeout,
                "last_error_type": None if last_error is None else last_error.__class__.__name__,
                "last_error_message": None if last_error is None else str(last_error),
                "retryable": True,
                **last_error_details,
            },
        )

    def _open_html_with_retry(self, url: str, *, referer: str | None = None) -> FetchResult:
        """Retry direct page opens, rebuilding the session after protocol errors."""
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._open_html(url, referer=referer)
            except (TimeoutError, curl_requests.errors.RequestsError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                if self._is_protocol_error(exc):
                    LOG.info(
                        "HTTP protocol error while opening %s, resetting session before retry %d/%d: %s",
                        url,
                        attempt + 1,
                        self.retries,
                        exc,
                    )
                    try:
                        self._reset_session()
                    except Exception:
                        LOG.warning("Failed to reset session after protocol error", exc_info=True)
                time.sleep(self._retry_delay(attempt))
        raise RemoteFetchError(
            f"failed to fetch {url} after {self.retries + 1} attempt(s) with timeout={self.timeout}s: {last_error}",
            details={
                "url": url,
                "attempts": self.retries + 1,
                "timeout_seconds": self.timeout,
                "last_error_type": None if last_error is None else last_error.__class__.__name__,
                "last_error_message": None if last_error is None else str(last_error),
                "retryable": True,
            },
        )

    def _open_html(self, url: str, *, referer: str | None = None) -> FetchResult:
        headers = self._request_headers(referer=referer)
        resp = self._session.get(url, headers=headers, timeout=self.timeout)
        html = _decode_response_body(resp.content, resp.headers.get("Content-Encoding", ""))

        try:
            nox_cookies = solve_nox_challenge_if_present(
                html,
                page_url=str(resp.url),
                user_agent=headers["User-Agent"],
                cookie_pairs=list(self._session.cookies.items()),
                fetch_script=lambda script_url: self._fetch_waf_script(script_url, referer=str(resp.url)),
            )
        except WafChallengeError as exc:
            raise RemoteFetchError(
                f"failed to solve Baidu WAF challenge for {resp.url}: {exc}",
                details={"url": str(resp.url), "status_code": resp.status_code, "retryable": False},
            ) from exc
        if nox_cookies is not None:
            self._install_cookie_pairs(nox_cookies, url=str(resp.url))
            self._save_cookies()
            resp = self._session.get(url, headers=headers, timeout=self.timeout)
            html = _decode_response_body(resp.content, resp.headers.get("Content-Encoding", ""))

        # acw_sc__v2 CF challenge — solve inline and retry once
        cookie_value = solve_acw_sc__v2_if_present(html)
        if cookie_value is not None:
            LOG.info("Solving acw_sc__v2 challenge for %s", url)
            self._session.cookies.delete("acw_sc__v2")
            self._session.cookies.set("acw_sc__v2", cookie_value, domain="bbs.yamibo.com", path="/")
            self._save_cookies()
            resp2 = self._session.get(url, headers=headers, timeout=self.timeout)
            html2 = _decode_response_body(resp2.content, resp2.headers.get("Content-Encoding", ""))
            return FetchResult(url=url, final_url=resp2.url, status_code=resp2.status_code, html=html2)

        return FetchResult(url=url, final_url=resp.url, status_code=resp.status_code, html=html)

    def _fetch_waf_script(self, url: str, *, referer: str) -> str:
        """Fetch a same-origin WAF script with the current session fingerprint."""
        headers = self._request_headers(referer=referer)
        response = self._session.get(url, headers=headers, timeout=self.timeout)
        if not 200 <= response.status_code < 400:
            raise RemoteFetchError(
                f"WAF challenge script returned HTTP {response.status_code} for {url}",
                details={"url": url, "status_code": response.status_code, "retryable": True},
            )
        return _decode_response_body(response.content, response.headers.get("Content-Encoding", ""))

    def _install_cookie_pairs(self, pairs: list[tuple[str, str]], *, url: str) -> None:
        hostname = urllib.parse.urlsplit(url).hostname or "bbs.yamibo.com"
        for name, value in pairs:
            self._session.cookies.delete(name)
            self._session.cookies.set(name, value, domain=hostname, path="/")

    def _base_url_for(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return "https://bbs.yamibo.com"

    # ── Login ────────────────────────────────────────────────────────────

    def _can_login(self) -> bool:
        return bool(self.login_username and self.login_password)

    def _login(self, *, base_url: str, referer: str | None = None) -> None:
        self._throttle()
        login_page_url = f"{base_url.rstrip('/')}/member.php?mod=logging&action=login"
        login_page = self._open_html_with_retry(login_page_url, referer=referer)
        if classify_html(login_page.html).page_type == PageType.REMOTE_MAINTENANCE:
            raise RemoteMaintenanceError(f"remote maintenance for {login_page.final_url}")
        action_url, formhash = _extract_login_form(login_page.html, base_url=base_url)
        payload = urllib.parse.urlencode({
            "formhash": formhash, "referer": referer or f"{base_url.rstrip('/')}/",
            "username": self.login_username or "", "password": self.login_password or "",
            "questionid": "0", "answer": "", "cookietime": "2592000", "loginsubmit": "yes",
        }).encode("utf-8")
        headers = {
            **self._request_headers(referer=login_page.final_url),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        resp = self._session.post(action_url, data=payload, headers=headers, timeout=self.timeout)
        try:
            resp.content  # consume body
        except Exception as exc:
            raise RemoteFetchError(f"failed to read login response: {exc}") from exc
        self._save_cookies()
        if not list(self._session.cookies.items()):
            raise LoginRequiredError(f"login failed for {base_url}")

    # ── Cookie persistence ───────────────────────────────────────────────

    def _load_cookies(self) -> None:
        if self.cookie_file is None or not self.cookie_file.exists():
            return
        cookies = _parse_cookie_text(self.cookie_file.read_text(encoding="utf-8", errors="ignore"))
        for name, value in cookies.items():
            if name in self._session.cookies:
                continue
            self._session.cookies.set(name, value, domain="bbs.yamibo.com", path="/")

    def _bootstrap_login_if_needed(self) -> None:
        if self.cookie_file is None or self.cookie_file.exists():
            return
        if list(self._session.cookies.items()):
            return
        if not self._can_login():
            return
        try:
            self._login(base_url="https://bbs.yamibo.com", referer="https://bbs.yamibo.com/")
        except Exception:
            LOG.warning("bootstrap login failed — continuing without cookies", exc_info=True)

    def _save_cookies(self) -> None:
        if self.cookie_file is None or not self.persist_cookies:
            return
        cookie_file = self.cookie_file
        with _cookie_file_lock(cookie_file):
            cookie_file.parent.mkdir(parents=True, exist_ok=True)
            cookies = _parse_cookie_text(
                cookie_file.read_text(encoding="utf-8", errors="ignore") if cookie_file.exists() else ""
            )
            cookies.update({name: value for name, value in self._session.cookies.items()})
            content = "\n".join(f"{name}={value}" for name, value in cookies.items())
            temp_file = cookie_file.with_name(f".{cookie_file.name}.{threading.get_ident()}.tmp")
            try:
                temp_file.write_text(content, encoding="utf-8")
                temp_file.replace(cookie_file)
            finally:
                try:
                    temp_file.unlink()
                except FileNotFoundError:
                    pass


# ── Standalone helpers ───────────────────────────────────────────────────────

def _decode_response_body(raw: bytes, content_encoding: str) -> str:
    encodings = [e.strip().lower() for e in content_encoding.split(",") if e.strip()]
    if "gzip" in encodings:
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore")


def _extract_login_form(html: str, *, base_url: str) -> tuple[str, str]:
    form_match = re.search(
        r'<form[^>]+action="([^"]*member\.php\?mod=logging[^"]*loginsubmit=yes[^"]*)"',
        html, flags=re.IGNORECASE,
    )
    formhash_match = re.search(r'name="formhash"\s+value="([^"]+)"', html, flags=re.IGNORECASE)
    if form_match is None or formhash_match is None:
        raise LoginRequiredError("could not parse login form")
    action_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", html_lib.unescape(form_match.group(1)))
    return action_url, formhash_match.group(1)


def _extract_formhash(html: str) -> str:
    match = re.search(r'name="formhash"\s+value="([^"]+)"', html, flags=re.IGNORECASE)
    if match is None:
        raise UnexpectedPageError("could not extract formhash from page")
    return match.group(1)


def _extract_html_title(html: str) -> str | None:
    """Extract <title> text from HTML for diagnostic purposes."""
    m = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


def _extract_discuz_prompt_text(html: str) -> str | None:
    prompt_match = re.search(
        r'<div[^>]+id=["\']messagetext["\'][^>]*>(.*?)</div>',
        html, flags=re.IGNORECASE | re.DOTALL,
    )
    if prompt_match is None:
        prompt_match = re.search(
            r'<div[^>]+class=["\'][^"\']*\balert_(?:error|info)\b[^"\']*["\'][^>]*>(.*?)</div>',
            html, flags=re.IGNORECASE | re.DOTALL,
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
    text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
    return text or None


def _extract_required_read_permission(prompt_text: str | None) -> int | None:
    if not prompt_text:
        return None
    match = re.search(r"阅读权限高于\s*(\d+)", prompt_text)
    if match is None:
        return None
    return int(match.group(1))


def _extract_search_page_urls(html: str, *, base_url: str) -> dict[int, str]:
    urls: dict[int, str] = {}
    for href, page_text in re.findall(r'<a href="([^"]*search\.php[^"]*page=(\d+)[^"]*)"', html, flags=re.IGNORECASE):
        resolved = urllib.parse.urljoin(base_url.rstrip("/") + "/", html_lib.unescape(href))
        urls[int(page_text)] = _rebase_url_to_base(resolved, base_url=base_url)
    return urls


def _search_page_url_from_first_result(first_page_url: str, *, page: int) -> str:
    parsed = urllib.parse.urlparse(first_page_url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query["page"] = [str(page)]
    new_query = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def _rebase_url_to_base(url: str, *, base_url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    base_parsed = urllib.parse.urlparse(base_url.rstrip("/"))
    if not parsed.scheme or not parsed.netloc:
        return urllib.parse.urljoin(base_url.rstrip("/") + "/", url)
    if parsed.netloc == base_parsed.netloc and parsed.scheme == base_parsed.scheme:
        return url
    return urllib.parse.urlunparse(
        parsed._replace(scheme=base_parsed.scheme or parsed.scheme, netloc=base_parsed.netloc or parsed.netloc)
    )
