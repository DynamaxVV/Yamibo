from __future__ import annotations

import re

# PONETAIL: heuristic cleaners — each rule guards with a line-length floor
# to avoid nuking multi-thousand-character novel chapters that happen to
# start or end with a pattern-like substring.
_MIN_LINE_LEN_TO_CLEAN = 120


def clean_content(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(
        r"(?mi)^\s*本帖最后由\s+.+?\s+于\s+\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+编辑\s*$",
        "",
        value,
    )
    value = _regex_sub_short_lines(
        value,
        r"\(\s*[\d.]+\s*(?:K|M|G)?B,\s*下载次数:\s*\d+\s*\)",
        flags=re.IGNORECASE,
    )
    value = _regex_sub_short_lines(value, r"下载附件\s*保存到相册")
    value = _regex_sub_short_lines(
        value,
        r"(?m)^\s*\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+上传\s*$",
    )
    value = _regex_sub_short_lines(
        value,
        r"(?mi)^\s*[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|gif|webp)\s*$",
    )
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
