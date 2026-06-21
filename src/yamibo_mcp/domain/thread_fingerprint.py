from __future__ import annotations

import hashlib
import re


def normalize_floor_content(content: str | None) -> str:
    if not content:
        return ""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(content).splitlines()]
    return "\n".join(line for line in lines if line)


def floor_content_hash(content: str | None) -> str:
    normalized = normalize_floor_content(content)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
