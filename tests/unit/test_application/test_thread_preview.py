from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from yamibo_mcp.application.thread_preview import preview_thread_context
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot


def _make_settings(tmp_path: Path):
    return SimpleNamespace(db_path=str(tmp_path / "app.db"), data_dir=tmp_path / "data")


def _make_snapshot() -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="[组] 预览测试",
        display_title="预览测试",
        group_name="组",
        author_guess="作者",
        core_title_guess="预览测试",
        normalized_core_title="预览测试",
        series_key="预览测试",
        title_aliases=[],
        chapter_name=None,
        chapter_index=None,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=1.0,
        needs_review=False,
        parser_version="title-v1",
    )
    floor = FloorSnapshot(
        pid=1001,
        tid=42,
        floor_no=1,
        publisher="u1",
        content="正文",
        pub_time="2026-01-01 12:00",
        has_images=True,
        image_urls=["https://img.example.com/a.jpg"],
    )
    return ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/thread-42-1-1.html",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="u1",
        publisher_uid="100",
        pub_time="2026-01-01 12:00",
        permission=0,
        floors=[floor],
        image_count=1,
    )


def test_preview_thread_context_returns_obsidian_markdown(tmp_path):
    settings = _make_settings(tmp_path)
    db = MagicMock()
    db.close = MagicMock()
    repo = MagicMock()
    thread_row = {
        "tid": 42,
        "page_type": "thread_detail",
        "raw_title": "[组] 预览测试",
        "display_title": "预览测试",
        "publisher": "u1",
        "publisher_uid": "100",
        "pub_time": "2026-01-01 12:00",
        "permission": 0,
        "image_count": 1,
        "archive_status": "complete",
        "sync_time": "2026-01-01T12:00:00+00:00",
        "forum_id": 30,
    }
    title_row = {
        "raw_title": "[组] 预览测试",
        "display_title": "预览测试",
        "group_name": "组",
        "author_guess": "作者",
        "core_title_guess": "预览测试",
        "normalized_core_title": "预览测试",
        "series_key": "预览测试",
        "title_aliases_json": "[]",
        "chapter_name": None,
        "chapter_index": None,
        "chapter_index_end": None,
        "chapter_title": None,
        "subtitle": None,
        "tags_json": "[]",
        "confidence": 1.0,
        "parser_version": "title-v1",
        "needs_review": 0,
    }
    floor_row = {
        "pid": 1001,
        "tid": 42,
        "floor_no": 1,
        "publisher": "u1",
        "content": "正文",
        "pub_time": "2026-01-01 12:00",
        "has_images": 1,
        "publisher_uid": "100",
        "image_urls_json": '["https://img.example.com/a.jpg"]',
        "quote_text": None,
        "reply_text": None,
        "rich_body_html": None,
    }
    repo.get_thread.return_value = thread_row
    repo.get_title_parse.return_value = title_row
    repo.list_floors.return_value = [floor_row]
    with patch("yamibo_mcp.application.thread_preview.load_settings", return_value=settings), patch("yamibo_mcp.application.thread_preview.connect", return_value=db), patch("yamibo_mcp.application.thread_preview.ThreadsRepository", return_value=repo):
        result = preview_thread_context(tid=42, context_format_version="obsidian-md-v2")
    assert result.ok is True
    assert result.data["tid"] == 42
    assert result.data["markdown"].startswith("---\n")
    assert 'board: "漫画区"' in result.data["markdown"]
    assert 'reply_count: 0' in result.data["markdown"]
    assert 'archive_status: "complete"' in result.data["markdown"]
    assert "context_format_version: \"obsidian-md-v2\"" in result.data["markdown"]
    assert 'cleaner_version: "cleaner-1.2"' in result.data["markdown"]
    assert "context_source_hash:" in result.data["markdown"]
    assert "# 预览测试" in result.data["markdown"]


def test_preview_thread_context_archive_fallback(tmp_path):
    settings = _make_settings(tmp_path)
    db = MagicMock()
    db.close = MagicMock()
    repo = MagicMock()
    repo.get_thread.return_value = None
    repo.get_title_parse.return_value = None
    repo.list_floors.return_value = []
    with patch("yamibo_mcp.application.thread_preview.load_settings", return_value=settings), patch("yamibo_mcp.application.thread_preview.connect", return_value=db), patch("yamibo_mcp.application.thread_preview.ThreadsRepository", return_value=repo):
        try:
            preview_thread_context(tid=1)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")
