from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from yamibo_mcp.application.archive_queries import probe_archived_threads, read_archived_thread
from yamibo_mcp.db.repositories.content_blocks import ContentBlocksRepository
from yamibo_mcp.db.repositories.threads import ThreadsRepository
from yamibo_mcp.domain.models import ContentBlock, FloorSnapshot, ThreadSnapshot, TitleSnapshot


def _fake_settings(tmp_path: Path):
    settings = MagicMock()
    settings.db_path = str(tmp_path / "test.db")
    settings.data_dir = tmp_path / "data"
    settings.export_dir = tmp_path / "exports"
    settings.novel_txt_export_dir = tmp_path / "novel_exports"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    settings.novel_txt_export_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _seed_large_thread(conn, *, tid: int = 9001, floor_count: int = 105) -> None:
    title = TitleSnapshot(
        raw_title="[TestGroup] Long Thread",
        display_title="Long Thread",
        group_name="TestGroup",
        author_guess="Author",
        core_title_guess="Long Thread",
        normalized_core_title="longthread",
        series_key="longthread",
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
    floors = [
        FloorSnapshot(
            pid=10_000 + index,
            tid=tid,
            floor_no=index,
            publisher="author",
            content=f"floor {index}",
            pub_time="2026-01-01",
            has_images=False,
        )
        for index in range(1, floor_count + 1)
    ]
    snapshot = ThreadSnapshot(
        tid=tid,
        url=f"https://bbs.yamibo.com/forum.php?mod=viewthread&tid={tid}",
        page_type="thread_detail",
        raw_title=title.raw_title,
        display_title=title.display_title,
        title=title,
        publisher="author",
        publisher_uid="1",
        pub_time="2026-01-01",
        permission=0,
        floors=floors,
        image_count=0,
    )
    ThreadsRepository(conn).upsert_snapshot(snapshot, context_path=f"threads/{tid}/context.md")
    ContentBlocksRepository(conn).upsert_blocks(
        tid,
        [
            ContentBlock(
                block_id=f"b-{floor.pid}",
                pid=floor.pid,
                order_index=floor.floor_no,
                block_type="text",
                text=f"block {floor.floor_no}",
            )
            for floor in floors
        ],
    )


def test_read_archived_thread_content_returns_cursor_chunk(tmp_path, db):
    settings = _fake_settings(tmp_path)
    _seed_large_thread(db)

    with patch("yamibo_mcp.application.archive_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.archive_queries.connect", return_value=db):
        first = read_archived_thread(tid=9001, view="content")

    assert first.ok is True
    assert first.data["floor_count"] == 105
    assert len(first.data["floors"]) == 20
    assert len(first.data["content_blocks"]) == 20
    assert first.data["floors"][0]["floor_no"] == 1
    assert first.data["floors"][-1]["floor_no"] == 20
    assert first.data["has_more"] is True
    assert first.data["next_cursor"] == "offset:20"
    assert first.data["resource_hints"]["next_page_tool_call"]["args"]["cursor"] == "offset:20"


def test_read_archived_thread_content_cursor_reads_last_chunk(tmp_path, db):
    settings = _fake_settings(tmp_path)
    _seed_large_thread(db)

    with patch("yamibo_mcp.application.archive_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.archive_queries.connect", return_value=db):
        last = read_archived_thread(tid=9001, view="content", cursor="offset:100", chunk_size=20)

    assert last.ok is True
    assert len(last.data["floors"]) == 5
    assert len(last.data["content_blocks"]) == 5
    assert last.data["floors"][0]["floor_no"] == 101
    assert last.data["floors"][-1]["floor_no"] == 105
    assert last.data["has_more"] is False
    assert last.data["next_cursor"] is None


def test_probe_archived_threads_reports_local_archive_state(tmp_path, db):
    settings = _fake_settings(tmp_path)
    _seed_large_thread(db, tid=9002, floor_count=3)

    with patch("yamibo_mcp.application.archive_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.archive_queries.connect", return_value=db):
        result = probe_archived_threads(tids=[9002, 12345, 9002])

    assert result.ok is True
    assert result.data["count"] == 2
    items = {item["tid"]: item for item in result.data["items"]}
    archived = items[9002]
    missing = items[12345]
    assert archived["archived"] is True
    assert archived["archive_status"] == "complete"
    assert archived["local_floor_count"] == 3
    assert archived["local_reply_count"] == 2
    assert archived["local_last_pid"] == 10003
    assert archived["local_last_floor_no"] == 3
    assert archived["local_last_floor_pub_time"] == "2026-01-01"
    assert archived["local_last_reply_at"] == "2026-01-01"
    assert missing["archived"] is False
    assert missing["local_floor_count"] == 0
    assert missing["local_last_floor_pub_time"] is None
    assert missing["local_last_reply_at"] is None
