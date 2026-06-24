from __future__ import annotations

from yamibo_mcp.domain.models import FloorSnapshot, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.storage.images import download_images_to_staging
from yamibo_mcp.storage.paths import StoragePaths


def _make_snapshot() -> ThreadSnapshot:
    title = TitleSnapshot(
        raw_title="测试帖子",
        display_title="测试帖子",
        group_name=None,
        author_guess="作者",
        core_title_guess="测试帖子",
        normalized_core_title="测试帖子",
        series_key="测试帖子",
        title_aliases=[],
        chapter_name=None,
        chapter_index=None,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.9,
        needs_review=False,
        parser_version="title-v1",
    )
    floor = FloorSnapshot(
        pid=1001,
        tid=42,
        floor_no=1,
        publisher="作者",
        content="正文",
        pub_time="2026-01-01 00:00",
        has_images=True,
        image_urls=["https://img.example.com/a.jpg"],
    )
    return ThreadSnapshot(
        tid=42,
        url="https://bbs.yamibo.com/forum.php?mod=viewthread&tid=42",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="作者",
        publisher_uid="100",
        pub_time="2026-01-01 00:00",
        permission=0,
        floors=[floor],
        image_count=1,
    )


def test_download_images_to_staging_returns_partial_on_stage_timeout(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(data_dir, export_dir=tmp_path / "exports", novel_txt_export_dir=tmp_path / "novel_exports")

    result = download_images_to_staging(
        paths,
        "job-1",
        _make_snapshot(),
        timeout=1,
        retries=0,
        cookie_file=tmp_path / "cookie.txt",
        stage_deadline_seconds=1e-9,
    )

    assert result.stopped_reason == "stage_timeout"
    assert result.downloaded_count == 0
    assert result.missing_urls == []
