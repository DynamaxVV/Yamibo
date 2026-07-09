from __future__ import annotations

from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except PermissionError:
        path.write_text(content, encoding="utf-8")
