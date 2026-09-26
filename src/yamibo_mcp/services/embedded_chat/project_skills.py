from __future__ import annotations

from importlib.resources import files

SKILLS = {
    "forum-search": "远端论坛搜索、按页浏览与分区核对",
    "archive-export": "指定帖归档、更新、导出及 Job 验收",
    "discussion-research": "本地讨论原文证据、趋势与研究",
    "daily-report": "日报读取、覆盖说明与原文追问",
}


def _root():
    return files("yamibo_mcp.services.embedded_chat")


def soul() -> str:
    return _root().joinpath("SOUL.md").read_text(encoding="utf-8")


def list_skills() -> list[dict[str, str]]:
    return [{"name": name, "description": description} for name, description in SKILLS.items()]


def read_skill(name: str, offset: int = 0, *, chunk_size: int = 12000) -> dict:
    if name not in SKILLS or offset < 0:
        raise ValueError("INVALID_PROJECT_SKILL")
    content = _root().joinpath("skills", name + ".md").read_text(encoding="utf-8")
    return {
        "name": name,
        "description": SKILLS[name],
        "content": content[offset:offset + chunk_size],
        "next_offset": offset + chunk_size if len(content) > offset + chunk_size else None,
    }
