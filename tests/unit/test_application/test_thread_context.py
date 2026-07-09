from __future__ import annotations

from yamibo_mcp.application.thread_context import build_obsidian_context_payload
from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot


def _make_snapshot() -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="[组] 测试标题",
        display_title="测试标题",
        group_name="组",
        author_guess="作者",
        core_title_guess="测试标题",
        normalized_core_title="测试标题",
        series_key="测试标题",
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
        content="原帖由 某人 于 2024-01-01 12:00 发表\n正文",
        quote_text="引用内容",
        reply_text="正文",
        pub_time="2026-01-01 12:00",
        has_images=True,
        image_urls=["https://img.example.com/a.jpg"],
    )
    return ThreadSnapshot(
        tid=42,
        url=None,
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


def test_build_obsidian_context_payload_uses_shared_cleaner_output():
    metadata, cleaner_output = build_obsidian_context_payload(
        _make_snapshot(),
        forum_id=30,
        archive_status="partial",
        reply_count=9,
        archived_images={1001: ["images/a.jpg"]},
        context_source="test",
    )

    assert metadata["board"] == "漫画区"
    assert metadata["reply_count"] == 9
    assert metadata["archive_status"] == "partial"
    assert cleaner_output["cleaner_version"] == "cleaner-1.2"
    assert cleaner_output["source_hash"]
    assert cleaner_output["context_inputs"] == {"source": "test", "tid": 42, "forum_id": 30}
    assert cleaner_output["floors"][0]["cleaned_body"] == "正文"
    assert cleaner_output["floors"][0]["cleaned_quote"] == "引用内容"
    assert cleaner_output["floors"][0]["image_slots"][0]["local_path"] == "images/a.jpg"
