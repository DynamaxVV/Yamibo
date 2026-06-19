"""测试数据加载器 - 数据驱动测试 (DDT)"""

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent


def load_fixture(name: str) -> dict[str, Any]:
    """加载测试数据"""
    path = FIXTURES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_forum_page(page: int) -> dict[str, Any]:
    """加载论坛列表页数据"""
    return load_fixture(f"forum_pages/page_{page}.json")


def load_thread(tid: int) -> dict[str, Any]:
    """加载帖子详情数据"""
    return load_fixture(f"threads/thread_{tid}.json")


def load_title_parse(name: str) -> dict[str, Any]:
    """加载标题解析数据"""
    return load_fixture(f"title_parses/{name}.json")


def load_edge_case(name: str) -> dict[str, Any]:
    """加载边界条件数据"""
    return load_fixture(f"edge_cases/{name}.json")


def get_all_forum_pages() -> list[dict[str, Any]]:
    """获取所有论坛页面数据"""
    pages_dir = FIXTURES_DIR / "forum_pages"
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(pages_dir.glob("page_*.json"))
    ]


def get_all_threads() -> list[dict[str, Any]]:
    """获取所有帖子详情数据"""
    threads_dir = FIXTURES_DIR / "threads"
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(threads_dir.glob("thread_*.json"))
    ]


def get_all_title_parses() -> list[dict[str, Any]]:
    """获取所有标题解析数据"""
    parses_dir = FIXTURES_DIR / "title_parses"
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(parses_dir.glob("*.json"))
    ]


def get_all_edge_cases() -> list[dict[str, Any]]:
    """获取所有边界条件数据"""
    edge_dir = FIXTURES_DIR / "edge_cases"
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(edge_dir.glob("*.json"))
    ]
