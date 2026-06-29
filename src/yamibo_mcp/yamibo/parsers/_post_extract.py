from __future__ import annotations

import re
from html import unescape as html_unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse


def _extract_post_block(html: str, *, pid: int) -> str | None:
    start_marker = f'id="postmessage_{pid}"'
    start = html.find(start_marker)
    if start < 0:
        return None
    end_marker = f'id="comment_{pid}"'
    end = html.find(end_marker, start)
    if end < 0:
        next_post = re.search(r'<div id="post_\d+"', html[start:], re.S)
        end = start + next_post.start() if next_post and next_post.start() > 0 else len(html)
    return html[start:end]


def _html_fragment_to_text(fragment: str) -> str:
    text = fragment
    replacements = (
        ("<br>", "\n"),
        ("<br/>", "\n"),
        ("<br />", "\n"),
        ("</tr>", "\n"),
        ("</table>", "\n"),
        ("</div>", "\n"),
        ("</p>", "\n"),
        ("</label>", "\n"),
        ("</li>", "\n"),
        ("</td>", "\t"),
    )
    for old, new in replacements:
        text = re.sub(old, new, text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_floor_image_urls_from_post_block(html: str, *, pid: int, base_url: str | None) -> list[str]:
    block = _extract_post_block(html, pid=pid)
    if not block:
        return []
    image_urls: list[str] = []
    for tag in re.findall(r"<img\b[^>]*>", block, flags=re.IGNORECASE):
        image_url = _resolve_image_url_from_tag(tag, base_url=base_url)
        if image_url:
            image_urls.append(image_url)
    return image_urls


def _extract_poll_text_from_post_block(html: str, *, pid: int) -> str | None:
    block = _extract_post_block(html, pid=pid)
    if not block:
        return None
    match = re.search(r'<form\b[^>]*\bid="poll"[^>]*>(?P<body>.*?)</form>', block, re.S | re.IGNORECASE)
    if match is None:
        return None
    body = match.group("body")
    lines: list[str] = []
    header_match = re.search(r'<div class="pinf">(.*?)</div>', body, re.S | re.IGNORECASE)
    if header_match is not None:
        header_text = _html_fragment_to_text(header_match.group(1))
        if header_text:
            lines.extend(header_text.splitlines())
    option_matches = re.findall(r'<label[^>]*>(.*?)</label>', body, re.S | re.IGNORECASE)
    for option in option_matches:
        option_text = _html_fragment_to_text(option)
        if option_text:
            lines.append(option_text)
    closing_text = _html_fragment_to_text(body)
    if "该投票已经关闭或者过期，不能投票" in closing_text and "该投票已经关闭或者过期，不能投票" not in lines:
        lines.append("该投票已经关闭或者过期，不能投票")
    cleaned = "\n".join(line.strip() for line in lines if line.strip())
    return cleaned or None


def _extract_post_meta(html: str) -> dict[int, dict[str, str | None]]:
    from yamibo_mcp.yamibo.title.normalizer import normalize_display_title

    meta: dict[int, dict[str, str | None]] = {}
    block_pattern = re.compile(r'<div id="post_(?P<pid>\d+)"[^>]*>(?P<body>.*?)(?=<div id="post_\d+"|$)', re.S)
    publisher_pattern = re.compile(
        r'<div class="authi"><a href="[^"]*space-uid-(?P<uid>\d+)\.html"[^>]*>(?P<publisher>.*?)</a>',
        re.S,
    )
    pub_time_pattern = re.compile(r'<em id="authorposton(?P<pid>\d+)">发表于 (?P<pub_time>\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})</em>')
    for match in block_pattern.finditer(html):
        pid = int(match.group("pid"))
        body = match.group("body")
        publisher_match = publisher_pattern.search(body)
        pub_time_match = pub_time_pattern.search(body)
        if not publisher_match and not pub_time_match:
            continue
        meta[pid] = {
            "publisher": None if publisher_match is None else normalize_display_title(publisher_match.group("publisher")),
            "publisher_uid": None if publisher_match is None else publisher_match.group("uid"),
            "pub_time": None if pub_time_match is None else pub_time_match.group("pub_time"),
        }
    return meta


def _extract_attachment_download_map(html: str, *, base_url: str | None) -> dict[str, str]:
    if not base_url:
        return {}
    try:
        urlparse(base_url)
    except ValueError:
        return {}
    aid_to_file: dict[str, str] = {}
    for tag in re.findall(r"<img\b[^>]*>", html, flags=re.IGNORECASE):
        aid_match = re.search(r'\baid="(?P<aid>\d+)"', tag)
        file_match = re.search(r'\b(?:zoomfile|file)="(?P<file>[^"]+)"', tag)
        if aid_match is None or file_match is None:
            continue
        try:
            aid_to_file[aid_match.group("aid")] = urljoin(base_url, file_match.group("file"))
        except ValueError:
            continue

    result: dict[str, str] = {}
    tip_pattern = re.compile(
        r'<div[^>]+id="aimg_(?P<aid>\d+)_menu"[^>]*>.*?<a href="(?P<href>[^"]*forum\.php\?mod=attachment[^"]*nothumb=yes[^"]*)"',
        re.S | re.IGNORECASE,
    )
    for match in tip_pattern.finditer(html):
        aid = match.group("aid")
        source_url = aid_to_file.get(aid)
        if not source_url:
            continue
        try:
            result[source_url] = urljoin(base_url, match.group("href").replace("&amp;", "&"))
        except ValueError:
            continue
    return result


def _resolve_image_url_from_tag(tag: str, *, base_url: str | None) -> str | None:
    attrs = dict(re.findall(r'([a-zA-Z0-9_:-]+)="([^"]*)"', tag))
    return _resolve_image_url_from_attrs(attrs, base_url=base_url)


def _resolve_image_url_from_attrs(attrs: dict[str, str], *, base_url: str | None) -> str | None:
    preferred_local = _resolve_local_saved_image(attrs, base_url=base_url)
    if preferred_local is not None:
        return preferred_local
    raw = attrs.get("file") or attrs.get("zoomfile") or attrs.get("src") or attrs.get("data-src") or ""
    raw = raw.strip()
    if not raw or raw.lstrip("/").startswith(("data:", "javascript:")):
        return None
    try:
        resolved = urljoin(base_url, raw) if base_url else raw
        parsed = urlparse(resolved)
    except ValueError:
        return None
    if _is_embedded_image_url(resolved, parsed=parsed):
        return None
    return resolved


def _is_embedded_image_url(image_url: str, *, parsed=None) -> bool:
    parsed = parsed or urlparse(image_url)
    lower = image_url.strip().lower()
    if lower.startswith("data:"):
        return True
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower().startswith("data:"):
        return True
    return False


def _resolve_local_saved_image(attrs: dict[str, str], *, base_url: str | None) -> str | None:
    if not base_url:
        return None
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return None
    if parsed.scheme != "file":
        return None
    src = (attrs.get("src") or attrs.get("data-src") or "").strip()
    if not src or src.startswith(("data:", "javascript:")):
        return None
    try:
        resolved = urljoin(base_url, src)
        resolved_parsed = urlparse(resolved)
    except ValueError:
        return None
    if resolved_parsed.scheme != "file":
        return None
    local_path = Path(resolved_parsed.path)
    return resolved if local_path.exists() else None
