#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import html
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


BASE_URL = "https://bbs.yamibo.com"
LOGIN_PAGE_URL = f"{BASE_URL}/member.php?mod=logging&action=login"
SIGN_PAGE_URL = f"{BASE_URL}/plugin.php?id=zqlj_sign"
SIGN_FALLBACK_URL = f"{BASE_URL}/plugin.php?id=zqlj_sign&sign=2f1d801b"
DEFAULT_USERNAME = "your_username"
DEFAULT_PASSWORD = "your_password"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def build_opener() -> urllib.request.OpenerDirector:
    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [
        ("User-Agent", USER_AGENT),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
        ("Accept-Encoding", "gzip"),
        ("Connection", "keep-alive"),
    ]
    return opener


def fetch_text(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    data: bytes | None = None,
    referer: str | None = None,
) -> tuple[str, str]:
    request = urllib.request.Request(url, data=data)
    if referer:
        request.add_header("Referer", referer)
    if data is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with opener.open(request, timeout=30) as response:
        raw = response.read()
        if "gzip" in str(response.headers.get("Content-Encoding", "")).lower():
            raw = gzip.decompress(raw)
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="ignore"), response.geturl()


def extract_login_form(html_text: str) -> tuple[str, str]:
    form_match = re.search(
        r'<form[^>]+action="([^"]*member\.php\?mod=logging[^"]*loginsubmit=yes[^"]*)"',
        html_text,
        flags=re.IGNORECASE,
    )
    formhash_match = re.search(
        r'name="formhash"\s+value="([^"]+)"',
        html_text,
        flags=re.IGNORECASE,
    )
    if not form_match or not formhash_match:
        raise RuntimeError("无法解析登录表单，页面结构可能已变化。")
    action_url = urllib.parse.urljoin(BASE_URL + "/", html.unescape(form_match.group(1)))
    return action_url, formhash_match.group(1)


def has_captcha(html_text: str) -> bool:
    lowered = html_text.lower()
    return any(
        token in lowered
        for token in (
            "seccode",
            "secqaa",
            "verifycode",
            "checksec",
            "验证码",
            "updateseccode",
            "updatesecqaa",
        )
    )


def extract_captcha_fields(html_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    patterns = {
        "seccodehash": r'name="seccodehash"\s+value="([^"]+)"',
        "secqaahash": r'name="secqaahash"\s+value="([^"]+)"',
        "idhash": r'name="idhash"\s+value="([^"]+)"',
        "verifyhash": r'name="verifyhash"\s+value="([^"]+)"',
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, html_text, flags=re.IGNORECASE)
        if match:
            fields[name] = match.group(1)
    return fields


def resolve_captcha(opener: urllib.request.OpenerDirector, login_page_html: str) -> dict[str, str]:
    if not has_captcha(login_page_html):
        return {}

    fields = extract_captcha_fields(login_page_html)
    if not fields:
        raise RuntimeError("检测到验证码，但未能解析验证码字段。")

    auto_solver = "http://api.jfbym.com/api/YmServer/customApi"
    if auto_solver:
        print("检测到验证码，调用外部解题服务。", file=sys.stderr)

        image_match = re.search(
            r'(<img[^>]+src="([^"]*(?:seccode|secqaa)[^"]*)"[^>]*>)',
            login_page_html,
            flags=re.IGNORECASE,
        )
        if not image_match:
            raise RuntimeError("检测到验证码，但未找到验证码图片地址。")

        image_url = urllib.parse.urljoin(BASE_URL + "/", html.unescape(image_match.group(2)))
        image_request = urllib.request.Request(image_url)
        image_request.add_header("Referer", LOGIN_PAGE_URL)
        with opener.open(image_request, timeout=30) as response:
            image_bytes = response.read()

        payload = {
            "image": base64.b64encode(image_bytes).decode("ascii"),
            "token": "jm-0DXfn1OrJIy4WhpsfQUFDLR1YTGWeneYet7oqLss",
            "type": "10110",
        }
        if not payload["token"]:
            raise RuntimeError("未设置 YAMIBO_CAPTCHA_TOKEN。")

        request = urllib.request.Request(
            auto_solver,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Referer": LOGIN_PAGE_URL},
            method="POST",
        )
        with opener.open(request, timeout=60) as response:
            response_text = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="ignore")
        try:
            solved = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"验证码服务返回了无效 JSON: {response_text[:200]}") from exc
        if not isinstance(solved, dict) or solved.get("code") != 10000:
            raise RuntimeError(f"验证码服务识别失败: {solved}")
        data = solved.get("data") or []
        if not data or not isinstance(data, list) or not isinstance(data[0], dict) or not data[0].get("data"):
            raise RuntimeError(f"验证码服务响应格式不正确: {solved}")
        answer = str(data[0]["data"]).strip()
        if not answer:
            raise RuntimeError("验证码服务返回了空答案。")
    else:
        print("检测到验证码，正在进入人工输入流程。", file=sys.stderr)
        for key, value in fields.items():
            print(f"  {key}={value}", file=sys.stderr)

        image_match = re.search(
            r'(<img[^>]+src="([^"]*(?:seccode|secqaa)[^"]*)"[^>]*>)',
            login_page_html,
            flags=re.IGNORECASE,
        )
        if image_match:
            image_url = urllib.parse.urljoin(BASE_URL + "/", html.unescape(image_match.group(2)))
            image_request = urllib.request.Request(image_url)
            image_request.add_header("Referer", LOGIN_PAGE_URL)
            with opener.open(image_request, timeout=30) as response:
                image_bytes = response.read()
            suffix = ".png"
            with tempfile.NamedTemporaryFile(prefix="yamibo_captcha_", suffix=suffix, delete=False) as tmp:
                tmp.write(image_bytes)
                tmp_path = Path(tmp.name)
            print(f"验证码图片已保存到: {tmp_path}", file=sys.stderr)
            print("请打开图片并输入验证码。", file=sys.stderr)
        else:
            print("未找到验证码图片地址，请直接按页面提示输入。", file=sys.stderr)
        answer = input("验证码: ").strip()
        if not answer:
            raise RuntimeError("验证码为空。")

    if "seccodehash" in fields:
        fields["seccodeverify"] = answer
    elif "secqaahash" in fields:
        fields["secqaaverify"] = answer
    else:
        fields["seccodeverify"] = answer
    return fields


def login(opener: urllib.request.OpenerDirector, username: str, password: str) -> None:
    login_page_html, login_page_url = fetch_text(opener, LOGIN_PAGE_URL, referer=BASE_URL + "/")
    action_url, formhash = extract_login_form(login_page_html)
    form = {
        "formhash": formhash,
        "referer": BASE_URL + "/",
        "username": username,
        "password": password,
        "questionid": "0",
        "answer": "",
        "cookietime": "2592000",
        "loginsubmit": "yes",
    }
    form.update(resolve_captcha(opener, login_page_html))
    payload = urllib.parse.urlencode(form).encode("utf-8")
    response_html, final_url = fetch_text(opener, action_url, data=payload, referer=login_page_url)
    if is_login_required(response_html, final_url):
        raise RuntimeError("登录失败：论坛仍然要求登录。请检查用户名、密码、验证码，或页面规则是否变化。")


def is_login_required(html_text: str, final_url: str) -> bool:
    lowered = html_text.lower()
    return (
        "member.php?mod=logging" in final_url.lower()
        or "请先登录" in html_text
        or ("login" in lowered and "password" in lowered and "formhash" in lowered)
    )


def extract_sign_link(html_text: str) -> str | None:
    link_match = re.search(
        r'<a[^>]+href="([^"]*plugin\.php\?id=zqlj_sign[^"]*sign=[^"]+)"[^>]*>\s*点击打卡\s*</a>',
        html_text,
        flags=re.IGNORECASE,
    )
    if link_match:
        return urllib.parse.urljoin(BASE_URL + "/", html.unescape(link_match.group(1)))

    fallback_match = re.search(
        r'(plugin\.php\?id=zqlj_sign&amp;sign=[^"<\s]+)',
        html_text,
        flags=re.IGNORECASE,
    )
    if fallback_match:
        return urllib.parse.urljoin(BASE_URL + "/", html.unescape(fallback_match.group(1)))
    return None


def strip_html(fragment: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", fragment, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def extract_message(html_text: str) -> str:
    patterns = [
        r'<div[^>]+id=["\']messagetext["\'][^>]*>(.*?)</div>',
        r'<div[^>]+class=["\'][^"\']*\balert_(?:info|error|success)\b[^"\']*["\'][^>]*>(.*?)</div>',
        r'<div[^>]+class=["\'][^"\']*\bmsg\b[^"\']*["\'][^>]*>(.*?)</div>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            text = strip_html(match.group(1))
            if text:
                return text

    title_match = re.search(r"<title>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        text = strip_html(title_match.group(1))
        if text:
            return text

    body_match = re.search(r"<body[^>]*>(.*?)</body>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        text = strip_html(body_match.group(1))
        if text:
            return text[:500]

    return "未能从页面中提取到明确的打卡信息。"


def sign_in(opener: urllib.request.OpenerDirector) -> str:
    sign_page_html, sign_page_url = fetch_text(opener, SIGN_PAGE_URL, referer=BASE_URL + "/")
    if is_login_required(sign_page_html, sign_page_url):
        raise RuntimeError("进入打卡页后仍然显示需要登录。")

    sign_url = extract_sign_link(sign_page_html) or SIGN_FALLBACK_URL
    result_html, _ = fetch_text(opener, sign_url, referer=SIGN_PAGE_URL)
    return extract_message(result_html)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="登录百合会并执行每日打卡。")
    parser.add_argument("username", nargs="?", default=DEFAULT_USERNAME, help="用户名")
    parser.add_argument("password", nargs="?", default=DEFAULT_PASSWORD, help="密码")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    opener = build_opener()
    try:
        login(opener, args.username, args.password)
        message = sign_in(opener)
    except Exception as exc:
        print(f"打卡失败: {exc}", file=sys.stderr)
        return 1

    print("打卡结果:")
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
