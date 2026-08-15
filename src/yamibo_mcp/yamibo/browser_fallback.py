from __future__ import annotations

import logging
import os
import shutil
import urllib.parse
from pathlib import Path
from threading import Lock

from playwright.sync_api import sync_playwright

from yamibo_mcp.config import Settings
from yamibo_mcp.errors import LoginRequiredError, RemoteFetchError
from yamibo_mcp.yamibo.anti_bot import is_soft_block_page
from yamibo_mcp.yamibo.client import FetchResult, YamiboClient
from yamibo_mcp.yamibo.page_classifier import PageType, classify_html
from yamibo_mcp.yamibo.parsers.forum_list import (
    ForumThreadItem,
    extract_total_pages,
    parse_forum_list,
)
from yamibo_mcp.yamibo.urls import (
    DEFAULT_FORUM_ID,
    dateline_forum_page_url,
    forum_page_url,
    thread_page_url_from_tid,
)

LOG = logging.getLogger(__name__)
_BROWSER_LOCK = Lock()
_MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _chrome_executable() -> str:
    configured = os.environ.get("YAMIBO_BROWSER_EXECUTABLE")
    if configured:
        return str(Path(configured).expanduser())
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        resolved = shutil.which(command)
        if resolved:
            return resolved
    # PONETAIL: macOS Chrome is the local deployment default; other hosts set the env var above.
    if _MACOS_CHROME.exists():
        return str(_MACOS_CHROME)
    raise RemoteFetchError("browser fallback requires Chrome or YAMIBO_BROWSER_EXECUTABLE")


def _login(page, *, base_url: str, username: str | None, password: str | None) -> None:
    login_url = f"{base_url.rstrip('/')}/member.php?mod=logging&action=login"
    page.goto(login_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    form = page.locator('form[name="login"]').first
    if form.count() == 0:
        return
    if not username or not password:
        raise LoginRequiredError("browser fallback requires configured login credentials")
    form.locator('input[name="username"]').fill(username)
    form.locator('input[name="password"]').fill(password)
    form.locator('button[name="loginsubmit"]').click(no_wait_after=True)
    page.wait_for_timeout(5000)


def fetch_html_with_browser(
    *,
    url: str,
    settings: Settings,
    account_id: str,
    username: str | None,
    password: str | None,
    proxy_url: str | None,
) -> FetchResult:
    """Fetch a WAF-protected page through a persistent system Chrome profile."""
    profile_dir = settings.data_dir / "browser-profiles" / account_id
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = max(int(settings.request_timeout_seconds * 1000), 30_000)
    launch_options: dict[str, object] = {
        "executable_path": _chrome_executable(),
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy_url:
        launch_options["proxy"] = {"server": proxy_url}

    with _BROWSER_LOCK, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(profile_dir), **launch_options)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(timeout_ms)
            parsed_url = urllib.parse.urlsplit(url)
            _login(
                page,
                base_url=f"{parsed_url.scheme}://{parsed_url.netloc}",
                username=username,
                password=password,
            )
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            html = page.content()
            result = FetchResult(
                url=url,
                final_url=page.url,
                status_code=response.status if response else 200,
                html=html,
            )
        finally:
            context.close()

    if is_soft_block_page(result.html):
        raise RemoteFetchError(f"browser fallback remained blocked for {result.final_url}")
    return result


def sign_daily_checkin_with_browser(
    *,
    page_url: str,
    action_url: str,
    settings: Settings,
    account_id: str,
    username: str | None,
    password: str | None,
    proxy_url: str | None,
) -> FetchResult:
    """Open the sign-in page and follow its action through persistent Chrome."""
    profile_dir = settings.data_dir / "browser-profiles" / account_id
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = max(int(settings.request_timeout_seconds * 1000), 30_000)
    launch_options: dict[str, object] = {
        "executable_path": _chrome_executable(),
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if proxy_url:
        launch_options["proxy"] = {"server": proxy_url}

    with _BROWSER_LOCK, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(profile_dir), **launch_options)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(timeout_ms)
            parsed_url = urllib.parse.urlsplit(page_url)
            _login(
                page,
                base_url=f"{parsed_url.scheme}://{parsed_url.netloc}",
                username=username,
                password=password,
            )
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            action = page.locator(f'a[href="{action_url}"]').first
            if action.count() > 0:
                action.click(no_wait_after=True)
            else:
                page.goto(action_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            html = page.content()
            result = FetchResult(
                url=action_url,
                final_url=page.url,
                status_code=200,
                html=html,
            )
        finally:
            context.close()

    if is_soft_block_page(result.html):
        raise RemoteFetchError(f"browser fallback remained blocked for {result.final_url}")
    if classify_html(result.html).page_type == PageType.LOGIN_REQUIRED:
        raise LoginRequiredError(f"login required for {result.final_url}")
    return result


class BrowserFallbackClient:
    """Use Chrome only when the regular client reports an anti-bot soft block."""

    def __init__(
        self,
        client: YamiboClient,
        *,
        settings: Settings,
        account_id: str,
        username: str | None,
        password: str | None,
        proxy_url: str | None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._account_id = account_id
        self._username = username
        self._password = password
        self._proxy_url = proxy_url

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def _fetch(self, url: str, validator) -> FetchResult:
        LOG.info("Falling back to browser fetch for %s", url)
        result = fetch_html_with_browser(
            url=url,
            settings=self._settings,
            account_id=self._account_id,
            username=self._username,
            password=self._password,
            proxy_url=self._proxy_url,
        )
        validator(result)
        return result

    def sign_daily_checkin(self, *, page_url: str | None = None, action_url: str | None = None) -> FetchResult:
        resolved_page_url = page_url or self._client.SIGN_IN_PAGE_URL
        resolved_action_url = action_url or self._client.SIGN_IN_ACTION_URL
        try:
            return self._client.sign_daily_checkin(
                page_url=resolved_page_url,
                action_url=resolved_action_url,
            )
        except RemoteFetchError as exc:
            if not self._is_soft_block(exc):
                raise
            LOG.info("Falling back to browser sign-in for %s", resolved_page_url)
            return sign_daily_checkin_with_browser(
                page_url=resolved_page_url,
                action_url=resolved_action_url,
                settings=self._settings,
                account_id=self._account_id,
                username=self._username,
                password=self._password,
                proxy_url=self._proxy_url,
            )

    @staticmethod
    def _is_soft_block(exc: RemoteFetchError) -> bool:
        return "soft block" in str(exc).lower()

    def fetch_forum_threads(
        self,
        *,
        page: int | None = None,
        url: str | None = None,
        base_url: str | None = None,
        forum_id: int = DEFAULT_FORUM_ID,
    ) -> tuple[FetchResult, list[ForumThreadItem]]:
        try:
            return self._client.fetch_forum_threads(
                page=page, url=url, base_url=base_url, forum_id=forum_id
            )
        except RemoteFetchError as exc:
            if not self._is_soft_block(exc):
                raise
            resolved_base = base_url or "https://bbs.yamibo.com"
            target = url or forum_page_url(page or 1, forum_id=forum_id, base_url=resolved_base)
            result = self._fetch(target, self._client._validate_forum_page)
            return result, parse_forum_list(result.html)

    def fetch_forum_threads_dateline(
        self,
        *,
        page: int,
        base_url: str | None = None,
        forum_id: int = DEFAULT_FORUM_ID,
    ) -> tuple[FetchResult, list[ForumThreadItem], int]:
        try:
            return self._client.fetch_forum_threads_dateline(
                page=page, base_url=base_url, forum_id=forum_id
            )
        except RemoteFetchError as exc:
            if not self._is_soft_block(exc):
                raise
            target = dateline_forum_page_url(
                page, forum_id=forum_id, base_url=base_url or "https://bbs.yamibo.com"
            )
            result = self._fetch(target, self._client._validate_forum_page)
            return result, parse_forum_list(result.html), extract_total_pages(result.html)

    def fetch_thread_page(
        self,
        *,
        tid: int,
        page: int,
        author_uid: str | None = None,
        base_url: str | None = None,
    ) -> FetchResult:
        try:
            return self._client.fetch_thread_page(
                tid=tid, page=page, author_uid=author_uid, base_url=base_url
            )
        except RemoteFetchError as exc:
            if not self._is_soft_block(exc):
                raise
            target = thread_page_url_from_tid(
                tid,
                page=page,
                author_uid=author_uid,
                base_url=base_url or "https://bbs.yamibo.com",
            )
            return self._fetch(target, self._client._validate_thread_page)
