import json

from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.markdown import render_thread_markdown, render_thread_metadata


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
