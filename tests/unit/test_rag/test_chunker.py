from __future__ import annotations

from types import SimpleNamespace

from yamibo_mcp.rag.chunker import build_rag_chunks


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=80,
    )


def test_chunker_ignores_image_only_floor():
    thread_row = {
        "tid": 1,
        "raw_title": "测试帖子",
        "display_title": "测试帖子",
        "forum_id": 30,
        "content_kind": "comic",
        "series_id": None,
        "publisher": "lz",
        "pub_time": "2026-01-01",
        "category": "漫画区",
    }
    title_row = {
        "group_name": "组",
        "author_guess": "作者",
        "core_title_guess": "测试帖子",
        "series_key": "测试帖子",
        "chapter_name": "第1话",
        "chapter_title": None,
        "chapter_index": 1.0,
    }
    floor_rows = [
        {
            "pid": 11,
            "floor_no": 1,
            "publisher": "lz",
            "pub_time": "2026-01-01",
            "content": "",
            "quote_text": None,
            "reply_text": None,
        }
    ]

    chunks = build_rag_chunks(thread_row=thread_row, title_row=title_row, floor_rows=floor_rows, settings=_settings())

    assert [chunk.chunk_id for chunk in chunks] == ["thread:1:title"]


def test_chunker_splits_long_novel_floor_and_ids_are_stable():
    thread_row = {
        "tid": 2,
        "raw_title": "轻小说测试",
        "display_title": "轻小说测试",
        "forum_id": 55,
        "content_kind": "novel",
        "series_id": 9,
        "publisher": "lz",
        "pub_time": "2026-01-01",
        "category": "轻小说区",
    }
    title_row = {
        "group_name": None,
        "author_guess": "作者",
        "core_title_guess": "轻小说测试",
        "series_key": "轻小说测试",
        "chapter_name": "第1章",
        "chapter_title": "开始",
        "chapter_index": 1.0,
    }
    long_text = "第一段" * 20 + "\n\n" + "第二段" * 20 + "\n\n" + "第三段" * 20
    floor_rows = [
        {
            "pid": 22,
            "floor_no": 1,
            "publisher": "lz",
            "pub_time": "2026-01-01",
            "content": long_text,
            "quote_text": None,
            "reply_text": None,
        }
    ]

    first = build_rag_chunks(thread_row=thread_row, title_row=title_row, floor_rows=floor_rows, settings=_settings())
    second = build_rag_chunks(thread_row=thread_row, title_row=title_row, floor_rows=floor_rows, settings=_settings())

    floor_chunk_ids = [chunk.chunk_id for chunk in first if chunk.floor_no == 1]
    assert len(floor_chunk_ids) >= 2
    assert floor_chunk_ids == [chunk.chunk_id for chunk in second if chunk.floor_no == 1]
    assert all("thread:2:floor:1:part:" in chunk_id for chunk_id in floor_chunk_ids)


def test_chunker_splits_long_non_novel_floor_too():
    thread_row = {
        "tid": 3,
        "raw_title": "漫画测试",
        "display_title": "漫画测试",
        "forum_id": 30,
        "content_kind": "comic",
        "series_id": 9,
        "publisher": "lz",
        "pub_time": "2026-01-01",
        "category": "中文百合漫画区",
    }
    title_row = {
        "group_name": None,
        "author_guess": "作者",
        "core_title_guess": "漫画测试",
        "series_key": "漫画测试",
        "chapter_name": "第1话",
        "chapter_title": "开始",
        "chapter_index": 1.0,
    }
    long_text = "这是一段很长的非小说正文。" * 20 + "\n\n" + "第二段内容。" * 20
    floor_rows = [
        {
            "pid": 33,
            "floor_no": 1,
            "publisher": "lz",
            "pub_time": "2026-01-01",
            "content": long_text,
            "quote_text": None,
            "reply_text": None,
        }
    ]

    chunks = build_rag_chunks(thread_row=thread_row, title_row=title_row, floor_rows=floor_rows, settings=_settings())

    floor_chunks = [chunk for chunk in chunks if chunk.floor_no == 1]
    assert len(floor_chunks) >= 2
    assert all(len(chunk.text) <= 80 for chunk in floor_chunks)
