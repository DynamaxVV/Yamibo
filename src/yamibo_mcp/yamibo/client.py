from __future__ import annotations

import html as html_lib
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.cookiejar import Cookie
from http.cookiejar import CookieJar
from pathlib import Path

from yamibo_mcp.errors import LoginRequiredError, RemoteFetchError, RemoteMaintenanceError, UnexpectedPageError
from yamibo_mcp.yamibo.parsers.forum_list import ForumThreadItem, parse_forum_list
from yamibo_mcp.yamibo.parsers.search_results import SearchResultItem, parse_search_results
from yamibo_mcp.yamibo.page_classifier import PageType, classify_html
from yamibo_mcp.yamibo.urls import forum_page_url, normalize_forum_page_url, normalize_thread_url, thread_url_from_tid


DEFAULT_HEADERS = {
    "User-Agent": "YamiboMCP/0.1 (+https://bbs.yamibo.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    html: str


class YamiboClient:
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
        login_username: str | None = None,
        login_password: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self.cookie_jar = cookie_jar or CookieJar()
        self.cookie_file = Path(cookie_file).expanduser() if cookie_file else None
        self.persist_cookies = persist_cookies or self.cookie_file is not None
        self.use_system_proxy = use_system_proxy
        self.login_username = login_username
        self.login_password = login_password
        handlers = [urllib.request.HTTPCookieProcessor(self.cookie_jar)]
        if not use_system_proxy:
            # 默认绕过系统代理，避免本机残留的 localhost 代理配置把抓取请求拦死。
            handlers.insert(0, urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)
        self._load_cookies()

    def fetch_url(self, url: str) -> FetchResult:
        return self._fetch_with_validation(url, self._validate_thread_page)

    def _fetch_with_validation(self, url: str, validator, *, allow_login_retry: bool = True) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                result = self._open_html(url)
                validator(result)
                self._save_cookies()
                return result
            except LoginRequiredError as exc:
                last_error = exc
                if allow_login_retry and self._can_login():
                    self._login(base_url=self._base_url_for(url), referer=url)
                    return self._fetch_with_validation(url, validator, allow_login_retry=False)
                break
            except urllib.error.HTTPError as exc:
                last_error = exc
                if attempt >= self.retries or exc.code < 500:
                    break
                time.sleep(min(0.25 * (attempt + 1), 1.0))
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(0.25 * (attempt + 1), 1.0))
        if isinstance(last_error, LoginRequiredError):
            raise last_error
        raise RemoteFetchError(f"failed to fetch {url}: {last_error}")

    def fetch_thread_by_tid(self, tid: int, *, base_url: str | None = None) -> FetchResult:
        return self.fetch_url(thread_url_from_tid(tid, base_url=base_url or "https://bbs.yamibo.com"))

    def fetch_thread(self, *, tid: int | None = None, url: str | None = None, base_url: str | None = None) -> FetchResult:
        if url:
            normalized = normalize_thread_url(url, base_url=base_url or "https://bbs.yamibo.com")
            return self.fetch_url(normalized)
        if tid is None:
            raise ValueError("fetch_thread requires tid or url")
        return self.fetch_thread_by_tid(tid, base_url=base_url)

    def fetch_forum_page(self, *, page: int | None = None, url: str | None = None, base_url: str | None = None) -> FetchResult:
        if url:
            normalized = normalize_forum_page_url(url, base_url=base_url or "https://bbs.yamibo.com")
        else:
            if page is None:
                raise ValueError("fetch_forum_page requires page or url")
            normalized = forum_page_url(page, base_url=base_url or "https://bbs.yamibo.com")
        result = self.fetch_url_allowing_forum_list(normalized)
        return result

    def fetch_forum_threads(self, *, page: int | None = None, url: str | None = None, base_url: str | None = None) -> tuple[FetchResult, list[ForumThreadItem]]:
        result = self.fetch_forum_page(page=page, url=url, base_url=base_url)
        return result, parse_forum_list(result.html)

    def fetch_search_results(
        self,
        *,
        query: str,
        base_url: str | None = None,
        forum_id: int = 30,
    ) -> tuple[FetchResult, list[SearchResultItem]]:
        resolved_base = (base_url or "https://bbs.yamibo.com").rstrip("/")
        forum_result = self.fetch_forum_page(page=1, base_url=resolved_base)
        formhash = _extract_formhash(forum_result.html)
        payload = urllib.parse.urlencode(
            {
                "mod": "curforum",
                "formhash": formhash,
                "srchtype": "title",
                "srhfid": str(forum_id),
                "srhlocality": "forum::forumdisplay",
                "srchtxt": query,
                "searchsubmit": "true",
            }
        ).encode("utf-8")
        search_url = f"{resolved_base}/search.php?searchsubmit=yes"
        request = urllib.request.Request(
            search_url,
            data=payload,
            headers={
                **self.headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": forum_result.final_url,
            },
            method="POST",
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            html_text = response.read().decode("utf-8", errors="ignore")
            result = FetchResult(
                url=search_url,
                final_url=response.geturl(),
                status_code=getattr(response, "status", 200),
                html=html_text,
            )
        self._validate_search_page(result)
        self._save_cookies()
        return result, parse_search_results(result.html)

    def fetch_search_results_all(
        self,
        *,
        query: str,
        base_url: str | None = None,
        forum_id: int = 30,
        start_page: int = 1,
        end_page: int | None = None,
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
            result = self.fetch_url_allowing_search(page_url)
            scanned_pages.append(result.final_url)
            add_items(parse_search_results(result.html))

        return collected, scanned_pages, total_pages

    def _validate_thread_page(self, result: FetchResult) -> None:
        # 这里只做最关键的页面级护栏，避免把登录页/维护页误当成帖子详情继续落库。
        classification = classify_html(result.html)
        if classification.page_type == PageType.LOGIN_REQUIRED:
            raise LoginRequiredError(f"login required for {result.final_url}")
        if classification.page_type == PageType.REMOTE_MAINTENANCE:
            raise RemoteMaintenanceError(f"remote maintenance for {result.final_url}")
        if classification.page_type != PageType.THREAD_DETAIL:
            raise UnexpectedPageError(
                f"expected thread detail page but got {classification.page_type.value} for {result.final_url}"
            )

    def fetch_url_allowing_forum_list(self, url: str) -> FetchResult:
        return self._fetch_with_validation(url, self._validate_forum_page)

    def fetch_url_allowing_search(self, url: str) -> FetchResult:
        return self._fetch_with_validation(url, self._validate_search_page)

    def _validate_forum_page(self, result: FetchResult) -> None:
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
        classification = classify_html(result.html)
        if classification.page_type == PageType.LOGIN_REQUIRED:
            raise LoginRequiredError(f"login required for {result.final_url}")
        if classification.page_type == PageType.REMOTE_MAINTENANCE:
            raise RemoteMaintenanceError(f"remote maintenance for {result.final_url}")
        if classification.page_type != PageType.SEARCH_RESULT:
            raise UnexpectedPageError(
                f"expected search result page but got {classification.page_type.value} for {result.final_url}"
            )

    def _open_html(self, url: str) -> FetchResult:
        request = urllib.request.Request(url, headers=self.headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
            return FetchResult(
                url=url,
                final_url=response.geturl(),
                status_code=getattr(response, "status", 200),
                html=html,
            )

    def _base_url_for(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return "https://bbs.yamibo.com"

    def _can_login(self) -> bool:
        return bool(self.login_username and self.login_password)

    def _login(self, *, base_url: str, referer: str | None = None) -> None:
        login_page_url = f"{base_url.rstrip('/')}/member.php?mod=logging&action=login"
        login_page = self._open_html(login_page_url)
        if classify_html(login_page.html).page_type == PageType.REMOTE_MAINTENANCE:
            raise RemoteMaintenanceError(f"remote maintenance for {login_page.final_url}")

        action_url, formhash = _extract_login_form(login_page.html, base_url=base_url)
        payload = urllib.parse.urlencode(
            {
                "formhash": formhash,
                "referer": referer or f"{base_url.rstrip('/')}/",
                "username": self.login_username or "",
                "password": self.login_password or "",
                "questionid": "0",
                "answer": "",
                "cookietime": "2592000",
                "loginsubmit": "yes",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            action_url,
            data=payload,
            headers={
                **self.headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": login_page.final_url,
            },
            method="POST",
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            response.read()
        self._save_cookies()
        if not list(self.cookie_jar):
            raise LoginRequiredError(f"login failed for {base_url}")

    def _load_cookies(self) -> None:
        if self.cookie_file is None or not self.cookie_file.exists():
            return
        raw = self.cookie_file.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw:
            return
        # 支持两种 MVP 格式：
        # 1. 一整串 Cookie header：a=1; b=2
        # 2. 每行一个 name=value
        if ";" in raw and "\n" not in raw:
            pairs = [segment.strip() for segment in raw.split(";") if "=" in segment]
        else:
            pairs = [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]
        for pair in pairs:
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            cookie = Cookie(
                version=0,
                name=name.strip(),
                value=value.strip(),
                port=None,
                port_specified=False,
                domain="bbs.yamibo.com",
                domain_specified=True,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=False,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
            self.cookie_jar.set_cookie(cookie)

    def _save_cookies(self) -> None:
        if self.cookie_file is None or not self.persist_cookies:
            return
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{cookie.name}={cookie.value}" for cookie in self.cookie_jar]
        self.cookie_file.write_text("\n".join(lines), encoding="utf-8")


def _extract_login_form(html: str, *, base_url: str) -> tuple[str, str]:
    form_match = re.search(
        r'<form[^>]+action="([^"]*member\.php\?mod=logging[^"]*loginsubmit=yes[^"]*)"',
        html,
        flags=re.IGNORECASE,
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
        parsed._replace(
            scheme=base_parsed.scheme or parsed.scheme,
            netloc=base_parsed.netloc or parsed.netloc,
        )
    )
