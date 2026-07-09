from __future__ import annotations

import re

# PONETAIL: heuristic cleaners — each rule guards with a line-length floor
# to avoid nuking multi-thousand-character novel chapters that happen to
# start or end with a pattern-like substring.
_MIN_LINE_LEN_TO_CLEAN = 120
_ATTACHMENT_FILE_EXTS = r"jpg|jpeg|png|gif|webp|bmp|txt|rar|zip|7z|pdf|doc|docx|xls|xlsx|ppt|pptx|epub|mobi|azw3|torrent"
_ATTACHMENT_SIZE_HINT_RE = re.compile(
    r"\(\s*[\d.]+\s*(?:Bytes?|[KMG]?B),\s*下载次数:\s*\d*\s*\)",
    re.IGNORECASE,
)
_ATTACHMENT_DOWNLOAD_PROMPT_RE = re.compile(r"(?:下载附件\s*保存到相册|点击文件名下载附件)")
_ATTACHMENT_DOWNLOAD_LINK_RE = re.compile(r"下载附件")
_ATTACHMENT_SAVE_ALBUM_RE = re.compile(r"保存到相册")
_ATTACHMENT_UPLOAD_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+上传")
_ATTACHMENT_FILENAME_LINE_RE = re.compile(
    rf"(?mi)^\s*[^<>]+?\.(?:{_ATTACHMENT_FILE_EXTS})\s*$"
)
_ATTACHMENT_INLINE_FILENAME_RE = re.compile(
    rf"(?<!\S)[^\s<>]+\.(?:{_ATTACHMENT_FILE_EXTS})(?!\S)",
    re.IGNORECASE,
)
_ATTACHMENT_INLINE_SUFFIX_RE = re.compile(
    rf"[A-Za-z0-9_\-[\](). ]{{1,160}}\.(?:{_ATTACHMENT_FILE_EXTS})\s*\(\s*[\d.]+\s*(?:Bytes?|[KMG]?B),\s*下载次数:\s*\d*\s*\)",
    re.IGNORECASE,
)
_ATTACHMENT_INLINE_SUFFIX_AFTER_BOUNDARY_RE = re.compile(
    rf"(?:(?<=^)|(?<=[\s>）】」』。，、！？；:：,.!?]))[^\s<>。！？；:：,，、]{{1,160}}\.(?:{_ATTACHMENT_FILE_EXTS})\s*\(\s*[\d.]+\s*(?:Bytes?|[KMG]?B),\s*下载次数:\s*\d*\s*\)",
    re.IGNORECASE,
)
_ATTACHMENT_TIMESTAMP_COUNT_LINE_RE = re.compile(
    r"(?mi)^\s*>?\s*\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s*,?\s*下载次数:\s*\d+\s*$"
)


def clean_content(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(
        r"(?mi)^\s*本帖最后由\s+.+?\s+于\s+\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+编辑\s*$",
        "",
        value,
    )
    value = _ATTACHMENT_INLINE_SUFFIX_RE.sub("", value)
    value = _ATTACHMENT_INLINE_SUFFIX_AFTER_BOUNDARY_RE.sub("", value)
    value = re.sub(_ATTACHMENT_TIMESTAMP_COUNT_LINE_RE, "", value)
    value = _clean_short_attachment_lines(value)
    value = re.sub(_ATTACHMENT_FILENAME_LINE_RE, "", value)
    value = re.sub(r"(?m)^[ \t\r]+$", "", value)
    value = re.sub(r"\r\n", "\n", value)
    value = re.sub(r"\r", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"(?m)^(Episode\s*\d+)(?=\S)", r"\1\n", value)
    value = re.sub(
        r"(?m)^(第\s*[0-9一二三四五六七八九十百千零0-9]+\s*[话章节卷篇])(?=\S)",
        r"\1\n",
        value,
    )
    value = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _clean_short_attachment_lines(text: str) -> str:
    lines = text.splitlines()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) > _MIN_LINE_LEN_TO_CLEAN:
            result.append(line)
            continue
        had_attachment_markers = any(
            pattern.search(line)
            for pattern in (
                _ATTACHMENT_SIZE_HINT_RE,
                _ATTACHMENT_DOWNLOAD_PROMPT_RE,
                _ATTACHMENT_DOWNLOAD_LINK_RE,
                _ATTACHMENT_SAVE_ALBUM_RE,
                _ATTACHMENT_UPLOAD_RE,
            )
        )
        cleaned = _ATTACHMENT_SIZE_HINT_RE.sub("", line)
        cleaned = _ATTACHMENT_DOWNLOAD_PROMPT_RE.sub("", cleaned)
        cleaned = _ATTACHMENT_DOWNLOAD_LINK_RE.sub("", cleaned)
        cleaned = _ATTACHMENT_SAVE_ALBUM_RE.sub("", cleaned)
        cleaned = _ATTACHMENT_UPLOAD_RE.sub("", cleaned)
        cleaned = re.sub(_ATTACHMENT_FILENAME_LINE_RE, "", cleaned)
        if had_attachment_markers:
            cleaned = _ATTACHMENT_INLINE_FILENAME_RE.sub("", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        result.append(cleaned)
    return "\n".join(result)


def _regex_sub_short_lines(
    text: str, pattern: str, flags: int = 0
) -> str:
    """Apply pattern only to lines whose cleaned length is <= _MIN_LINE_LEN_TO_CLEAN.

    Long lines (e.g. >120 chars of novel prose) are kept intact even if they
    accidentally match a noise pattern somewhere in the middle.
    """
    lines = text.splitlines()
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) > _MIN_LINE_LEN_TO_CLEAN:
            result.append(line)
        else:
            result.append(re.sub(pattern, "", line, flags=flags))
    return "\n".join(result)
