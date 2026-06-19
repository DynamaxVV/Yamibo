from __future__ import annotations

import re


def clean_content(value: str) -> str:
    value = value.replace("\xa0", " ")
    # 先去掉附件下载提示和裸文件名，再整理空白，尽量保留人真正会读的正文。
    value = re.sub(r"\(\s*[\d.]+\s*(?:K|M|G)?B,\s*下载次数:\s*\d+\s*\)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"下载附件\s*保存到相册", "", value)
    # 附件悬浮卡片里会有“2026-6-10 18:28 上传”这类纯元信息，整行删掉即可，避免污染正文。
    value = re.sub(r"(?m)^\s*\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s+上传\s*$", "", value)
    # 只移除独占一整行的附件文件名噪音，避免把“我看不懂但我大受震撼.jpg”这类正文梗句误删掉。
    value = re.sub(r"(?mi)^\s*[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|gif|webp)\s*$", "", value)
    value = re.sub(r"(?m)^[ \t\r]+$", "", value)
    value = re.sub(r"\r\n", "\n", value)
    value = re.sub(r"\r", "\n", value)
    value = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
