"""Solve the small JavaScript challenge returned by Baidu WAF.

The forum currently serves a ``nox_jst_v1`` challenge before normal HTML.  It
is a JavaScript fingerprint check, not a page interaction flow.  Running the
same challenge script in QuickJS keeps the HTTP client usable in a container
without a browser.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse
from collections.abc import Callable

import quickjs


class WafChallengeError(RuntimeError):
    """The remote challenge could not be evaluated or did not set its cookie."""


_NOX_SCRIPT_RE = re.compile(
    r"<script\b[^>]*\bsrc=[\"']([^\"']*nox_[^\"']+\.js)[\"']",
    flags=re.IGNORECASE,
)
_MAX_SCRIPT_CHARS = 1_000_000


_BROWSER_SHIM = r"""
(function () {
  var __cookie = __INITIAL_COOKIE__;
  function __set_cookie(value) {
    var pair = String(value).split(';', 1)[0];
    var index = pair.indexOf('=');
    if (index < 0) return;
    var name = pair.slice(0, index);
    var next = pair.slice(index + 1);
    var parts = __cookie ? __cookie.split(/;\s*/) : [];
    var found = false;
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].slice(0, name.length + 1) === name + '=') {
        parts[i] = name + '=' + next;
        found = true;
      }
    }
    if (!found) parts.push(name + '=' + next);
    __cookie = parts.filter(function (item) { return item; }).join('; ');
  }
  function __element(tag) {
    return {
      tagName: String(tag).toUpperCase(), style: {}, children: [], parentNode: null,
      appendChild: function (child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
      },
      removeChild: function () {}, setAttribute: function () {},
      getAttribute: function () { return null; },
      addEventListener: function () {}, removeEventListener: function () {},
      getContext: function () { return {}; }
    };
  }
  function __constructor() {}
  var document = {
    get cookie() { return __cookie; },
    set cookie(value) { __set_cookie(value); },
    body: __element('body'), head: __element('head'), documentElement: __element('html'),
    createElement: __element, querySelector: function () { return null; },
    getElementsByTagName: function () { return []; },
    addEventListener: function () {}, removeEventListener: function () {},
    referrer: '', readyState: 'complete', visibilityState: 'visible', hidden: false,
    domain: __HOSTNAME__
  };
  var location = {
    href: __LOCATION__, hostname: __HOSTNAME__, protocol: __PROTOCOL__,
    host: __HOST__, pathname: __PATHNAME__, search: __SEARCH__, hash: __HASH__,
    reload: function () {}, replace: function () {}, assign: function () {},
    toString: function () { return this.href; }
  };
  var window = globalThis;
  window.window = window; window.self = window; window.global = window;
  window.globalThis = window; window.document = document; window.location = location;
  window.navigator = {
    userAgent: __USER_AGENT__, language: 'zh-CN', languages: ['zh-CN', 'zh'],
    platform: 'MacIntel', cookieEnabled: true, webdriver: false,
    onLine: true, hardwareConcurrency: 4, maxTouchPoints: 0
  };
  window.screen = {
    width: 1440, height: 900, availWidth: 1440, availHeight: 860,
    colorDepth: 24, pixelDepth: 24
  };
  window.innerWidth = 1440; window.innerHeight = 900;
  window.outerWidth = 1440; window.outerHeight = 900; window.devicePixelRatio = 2;
  window.performance = { now: function () { return Date.now(); }, timing: {} };
  window.history = { length: 1, state: null, back: function () {},
    forward: function () {}, go: function () {} };
  window.top = window; window.parent = window; window.opener = null; window.frames = [];
  window.addEventListener = function () {}; window.removeEventListener = function () {};
  window.getComputedStyle = function () { return {}; };
  window.requestAnimationFrame = function (fn) {
    if (typeof fn === 'function') fn(Date.now());
    return 1;
  };
  window.setTimeout = function (fn) {
    if (typeof fn === 'function') fn();
    return 1;
  };
  window.clearTimeout = function () {};
  window.setInterval = function (fn) {
    if (typeof fn === 'function') fn();
    return 1;
  };
  window.clearInterval = function () {};
  window.console = { log: function () {}, error: function () {}, warn: function () {},
    info: function () {}, debug: function () {} };
  window.__noxExpire = 30; window.__noxDomain = ''; window.__noxImd = 1;
  window.Window = __constructor; window.Document = __constructor;
  window.HTMLDocument = __constructor; window.Element = __constructor;
  window.HTMLElement = __constructor; window.Node = __constructor;
  window.Event = __constructor; window.CustomEvent = __constructor;
  window.XMLHttpRequest = __constructor; window.WebSocket = __constructor;
  window.MutationObserver = __constructor; window.Image = __constructor;
  window.History = __constructor; window.Screen = __constructor;
  window.Location = __constructor; window.Performance = __constructor;
  window.Navigator = __constructor; window.NodeList = __constructor;
  window.DOMTokenList = __constructor; window.FormData = __constructor;
  window.__get_cookie = function () { return __cookie; };
}());
"""


def extract_nox_script_url(html: str, *, page_url: str) -> str | None:
    """Return the same-origin Nox script URL advertised by a challenge page."""
    match = _NOX_SCRIPT_RE.search(html)
    if match is None:
        return None
    script_url = urllib.parse.urljoin(page_url, html_lib.unescape(match.group(1)))
    page_parts = urllib.parse.urlsplit(page_url)
    script_parts = urllib.parse.urlsplit(script_url)
    if (script_parts.scheme, script_parts.netloc) != (page_parts.scheme, page_parts.netloc):
        raise WafChallengeError(f"refusing cross-origin WAF script: {script_url}")
    return script_url


def cookie_header_from_pairs(pairs: list[tuple[str, str]]) -> str:
    return "; ".join(f"{name}={value}" for name, value in _unique_cookie_pairs(pairs))


def parse_cookie_header(cookie_header: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw_pair in cookie_header.split(";"):
        if "=" not in raw_pair:
            continue
        name, value = raw_pair.strip().split("=", 1)
        if name:
            pairs.append((name, value))
    return _unique_cookie_pairs(pairs)


def _unique_cookie_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    values: dict[str, str] = {}
    for name, value in pairs:
        if name:
            values[name] = value
    return list(values.items())


def solve_nox_challenge(
    script_source: str,
    *,
    page_url: str,
    cookie_header: str,
    user_agent: str,
) -> str:
    """Execute one Nox script and return the resulting browser cookie header."""
    if len(script_source) > _MAX_SCRIPT_CHARS:
        raise WafChallengeError(f"WAF script is unexpectedly large: {len(script_source)} bytes")
    parsed = urllib.parse.urlsplit(page_url)
    replacements = {
        "__INITIAL_COOKIE__": json.dumps(cookie_header),
        "__LOCATION__": json.dumps(page_url),
        "__HOSTNAME__": json.dumps(parsed.hostname or ""),
        "__PROTOCOL__": json.dumps(f"{parsed.scheme}:"),
        "__HOST__": json.dumps(parsed.netloc),
        "__PATHNAME__": json.dumps(parsed.path or "/"),
        "__SEARCH__": json.dumps(f"?{parsed.query}" if parsed.query else ""),
        "__HASH__": json.dumps(f"#{parsed.fragment}" if parsed.fragment else ""),
        "__USER_AGENT__": json.dumps(user_agent),
    }
    shim = _BROWSER_SHIM
    for placeholder, value in replacements.items():
        shim = shim.replace(placeholder, value)
    context = quickjs.Context()
    try:
        context.set_time_limit(5.0)
        context.eval(shim)
        context.eval(script_source)
        result = str(context.eval("__get_cookie()"))
    except Exception as exc:  # quickjs exposes runtime errors as its own exception type
        raise WafChallengeError(f"failed to evaluate Nox challenge: {exc}") from exc
    if not any(name == "nox_jst_v1" for name, _ in parse_cookie_header(result)):
        raise WafChallengeError("Nox challenge completed without nox_jst_v1 cookie")
    return result


def solve_nox_challenge_if_present(
    html: str,
    *,
    page_url: str,
    user_agent: str,
    cookie_pairs: list[tuple[str, str]],
    fetch_script: Callable[[str], str],
) -> list[tuple[str, str]] | None:
    """Fetch and evaluate a Nox challenge, returning cookies to install."""
    script_url = extract_nox_script_url(html, page_url=page_url)
    if script_url is None:
        return None
    script_source = fetch_script(script_url)
    cookie_header = solve_nox_challenge(
        script_source,
        page_url=page_url,
        cookie_header=cookie_header_from_pairs(cookie_pairs),
        user_agent=user_agent,
    )
    return parse_cookie_header(cookie_header)
