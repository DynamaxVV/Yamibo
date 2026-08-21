import json
import re

from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.markdown import (
    CONTEXT_FORMAT_OBSIDIAN_V2,
    render_archive_markdown,
    render_obsidian_markdown,
    render_thread_markdown,
    render_thread_metadata,
)


def _make_snapshot(tid=999, floors=None, **overrides):
    title = TitleSnapshot(
        raw_title="[A组] 测试漫画 第1话", display_title="测试漫画 第1话",
        group_name="A组", author_guess="作者A",
        core_title_guess="测试漫画", normalized_core_title="测试漫画",
        series_key="测试漫画", title_aliases=[],
        chapter_name="第1话", chapter_index=1.0, chapter_index_end=None,
        chapter_title=None, subtitle=None, tags=[], confidence=0.9,
        needs_review=False, parser_version="title-v1",
    )
    floors = floors or [
        FloorSnapshot(pid=1001, tid=tid, floor_no=1, publisher="user1",
                      content="正文内容", pub_time="2025-01-01 12:00",
                      has_images=False, image_urls=[]),
    ]
    defaults = dict(
        tid=tid, url=None, page_type="thread_detail",
        raw_title=title.raw_title, display_title=title.display_title,
        title=title, publisher="user1", publisher_uid="1",
        pub_time="2025-01-01", permission=0, floors=floors, image_count=0,
    )
    defaults.update(overrides)
    return ThreadSnapshot(**defaults)


class TestRenderThreadMarkdown:
    def test_contains_frontmatter(self):
        md = render_thread_markdown(_make_snapshot())
        assert md.startswith("---\n")
        assert "tid: 999" in md

    def test_contains_title_heading(self):
        md = render_thread_markdown(_make_snapshot())
        assert "# 测试漫画 第1话" in md

    def test_contains_floor_content(self):
        md = render_thread_markdown(_make_snapshot())
        assert "正文内容" in md
        assert "1F · user1" in md

    def test_ignores_rich_body_html_for_txt_output(self):
        floor = FloorSnapshot(
            pid=1002,
            tid=999,
            floor_no=1,
            publisher="user1",
            content="纯文本正文",
            pub_time="2025-01-01 12:00",
            has_images=False,
            image_urls=[],
            rich_body_html='<div><font color="#ff0000"><strong>富文本</strong></font></div>',
        )
        md = render_thread_markdown(_make_snapshot(floors=[floor]))
        assert "纯文本正文" in md
        assert "富文本" not in md
        assert "<font" not in md
        assert "color=" not in md

    def test_image_only_floor_placeholder(self):
        floor = FloorSnapshot(pid=2001, tid=999, floor_no=1, publisher="u",
                              content="", pub_time=None, has_images=True,
                              image_urls=["http://img/a.jpg"])
        md = render_thread_markdown(_make_snapshot(floors=[floor]))
        assert "[image-only floor]" in md

    def test_archived_images_rendered(self):
        floor = FloorSnapshot(pid=3001, tid=999, floor_no=1, publisher="u",
                              content="内容", pub_time=None, has_images=True,
                              image_urls=["http://img/a.jpg"])
        md = render_thread_markdown(
            _make_snapshot(floors=[floor]),
            archived_images={3001: ["images/floor_001_01.jpg"]},
        )
        assert "![image](images/floor_001_01.jpg)" in md

    def test_ends_with_newline(self):
        md = render_thread_markdown(_make_snapshot())
        assert md.endswith("\n")


class TestRenderObsidianMarkdown:
    def test_frontmatter_and_body(self):
        floor = {
            "pid": 1,
            "floor_no": 1,
            "publisher": "用户_xxx",
            "pub_time": "2026-01-01 12:00",
            "cleaned_body": "正文1",
            "cleaned_quote": "",
            "quote_target_hints": [],
            "image_slots": [],
        }
        md = render_obsidian_markdown(
            _make_snapshot(),
            metadata={"board": "动漫区", "tags": ["[其他]"], "reply_count": 4, "archive_status": "complete"},
            cleaner_output={"cleaner_version": "cleaner-1.2", "source_hash": "abc", "floors": [floor]},
        )
        assert md.startswith("---\n")
        assert f"context_format_version: {json.dumps(CONTEXT_FORMAT_OBSIDIAN_V2, ensure_ascii=False)}" in md
        assert "# 测试漫画 第1话" in md
        assert "正文1" in md

    def test_frontmatter_contains_required_yaml_fields_and_partial_status(self):
        floor = {
            "pid": 1,
            "floor_no": 1,
            "publisher": "用户[]#|",
            "pub_time": "2026-01-01 12:00",
            "cleaned_body": "正文1",
            "cleaned_quote": "引用内容",
            "quote_target_hints": ["2F 用户B"],
            "image_slots": [{"local_path": "images/a.jpg"}],
        }
        md = render_obsidian_markdown(
            _make_snapshot(
                floors=[
                    FloorSnapshot(
                        pid=1001,
                        tid=999,
                        floor_no=1,
                        publisher="用户[]#|",
                        content="原始正文",
                        pub_time="2026-01-01 12:00",
                        has_images=True,
                        image_urls=["https://img.example/a.jpg"],
                    )
                ]
            ),
            metadata={
                "board": "海域区",
                "tags": ["[讨论]", "海域区"],
                "reply_count": 4,
                "archive_status": "partial",
                "sync_time": "2026-07-05T00:00:00+00:00",
                "ai_summary": "",
                "context_rendered_at": "2026-07-05T01:02:03+00:00",
                "context_inputs": {"source": "test"},
            },
            cleaner_output={"cleaner_version": "cleaner-1.2", "source_hash": "sha256:test", "floors": [floor]},
        )

        assert md.startswith("---\n")
        assert 'title: "测试漫画 第1话"' in md
        assert 'aliases: ["测试漫画 第1话", "[A组] 测试漫画 第1话"]' in md
        assert 'publisher: "user1"' in md
        assert 'reply_count: 4' in md
        assert 'board: "海域区"' in md
        assert 'tags: ["[讨论]", "海域区"]' in md
        assert 'archive_status: "partial"' in md
        assert 'sync_time: "2026-07-05T00:00:00+00:00"' in md
        assert f"context_format_version: {json.dumps(CONTEXT_FORMAT_OBSIDIAN_V2, ensure_ascii=False)}" in md
        assert 'cleaner_version: "cleaner-1.2"' in md
        assert 'context_source_hash: "sha256:test"' in md
        assert 'context_rendered_at: "2026-07-05T01:02:03+00:00"' in md
        assert '> [!quote] 引用 [[2F 用户B]]：' in md
        assert '![[images/a.jpg]]' in md
        assert '## 1F · [[用户____]] · 2026-01-01 12:00' in md
        assert "^f1" in md


class TestRenderThreadMetadata:
    def test_returns_valid_json(self):
        snapshot = _make_snapshot()
        result = render_thread_metadata(snapshot, "threads/999/context.md")
        data = json.loads(result)
        assert data["tid"] == 999
        assert data["context_path"] == "threads/999/context.md"

    def test_archived_images_in_metadata(self):
        snapshot = _make_snapshot()
        result = render_thread_metadata(
            snapshot, "ctx.md",
            archived_images={1001: ["images/floor_001_01.jpg"]},
        )
        data = json.loads(result)
        assert data["archived_images"]["1001"] == ["images/floor_001_01.jpg"]

    def test_missing_images_tracked(self):
        snapshot = _make_snapshot()
        result = render_thread_metadata(
            snapshot, "ctx.md",
            missing_image_urls=["http://img/missing.jpg"],
        )
        data = json.loads(result)
        assert "http://img/missing.jpg" in data["missing_image_urls"]

    def test_non_export_images_separated(self):
        snapshot = _make_snapshot()
        result = render_thread_metadata(
            snapshot, "ctx.md",
            non_export_images={1001: ["images/non_export.jpg"]},
        )
        data = json.loads(result)
        assert data["non_export_images"]["1001"] == ["images/non_export.jpg"]
        floor_data = data["floors"][0]
        assert "images/non_export.jpg" in floor_data["non_export_image_urls"]

    def test_floor_image_slots_preserve_missing_url_positions(self):
        floor = FloorSnapshot(
            pid=1001,
            tid=999,
            floor_no=1,
            publisher="u",
            content="内容",
            pub_time=None,
            has_images=True,
            image_urls=["http://img/a.jpg", "http://img/missing.jpg", "http://img/b.jpg"],
        )
        result = render_thread_metadata(
            _make_snapshot(floors=[floor]),
            "ctx.md",
            archived_images={1001: ["images/a.jpg", "images/b.jpg"]},
            missing_image_urls=["http://img/missing.jpg"],
        )
        data = json.loads(result)
        slots = data["floors"][0]["image_slots"]
        assert slots[0]["local_path"] == "images/a.jpg"
        assert slots[1]["remote_url"] == "http://img/missing.jpg"
        assert slots[1]["local_path"] is None
        assert slots[1]["status"] == "missing"
        assert slots[2]["local_path"] == "images/b.jpg"

    def test_floor_image_slot_overrides_keep_a_selected_download_at_its_original_position(self):
        urls = [f"https://img.example.com/{index}.jpg" for index in range(1, 66)]
        floor = FloorSnapshot(
            pid=1001,
            tid=999,
            floor_no=1,
            publisher="u",
            content="内容",
            pub_time=None,
            has_images=True,
            image_urls=urls,
        )
        overrides = {
            url: {
                "local_path": "images/floor_001_13.jpg" if index == 13 else None,
                "status": "non_export" if index == 13 else "missing",
            }
            for index, url in enumerate(urls, start=1)
        }
        result = render_thread_metadata(
            _make_snapshot(floors=[floor]),
            "ctx.md",
            non_export_images={1001: ["images/floor_001_13.jpg"]},
            image_slot_overrides=overrides,
        )
        slots = json.loads(result)["floors"][0]["image_slots"]
        assert slots[0] == {"remote_url": urls[0], "local_path": None, "status": "missing"}
        assert slots[12] == {
            "remote_url": urls[12],
            "local_path": "images/floor_001_13.jpg",
            "status": "non_export",
        }
        assert sum(slot["local_path"] is not None for slot in slots) == 1
