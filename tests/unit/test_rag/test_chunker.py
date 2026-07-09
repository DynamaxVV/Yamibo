from __future__ import annotations

from types import SimpleNamespace

from yamibo_mcp.rag.chunker import build_rag_chunks


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        rag_min_chunk_chars=20,
        rag_max_chunk_chars=80,
    )


def _thread_row(**overrides):
    row = {
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
    row.update(overrides)
    return row


def _title_row(**overrides):
    row = {
        "group_name": "组",
        "author_guess": "作者",
        "core_title_guess": "测试帖子",
        "series_key": "测试帖子",
        "chapter_name": "第1话",
        "chapter_title": None,
        "chapter_index": 1.0,
    }
    row.update(overrides)
    return row


def test_chunker_exposes_traceability_fields_for_title_chunk():
    chunks = build_rag_chunks(
        thread_row=_thread_row(),
        title_row=_title_row(),
        floor_rows=[],
        settings=_settings(),
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "thread:1:title:anime-evidence-lane-dry-run-v1"
    assert chunk.source_tid == 1
    assert chunk.source_pid is None
    assert chunk.source_floor_no is None
    assert chunk.cleaner_version == "anime-cleaner-1.2"
    assert chunk.chunker_version == "anime-chunker-1.2"
    assert chunk.materializer_version == "anime-rag-materializer-1.2"
    assert chunk.source_hash
    assert chunk.generated_at
    assert chunk.title == "测试帖子"


def test_chunker_preserves_stable_ids_and_shared_cleaner_payload():
    floor_rows = [
        {
            "pid": 22,
            "floor_no": 1,
            "publisher": "lz",
            "pub_time": "2026-01-01",
            "content": "第一段" * 20 + "\n\n" + "第二段" * 20 + "\n\n" + "第三段" * 20,
            "quote_text": None,
            "reply_text": None,
        }
    ]

    first = build_rag_chunks(thread_row=_thread_row(tid=2, display_title="轻小说测试", raw_title="轻小说测试", forum_id=55, content_kind="novel", series_id=9), title_row=_title_row(core_title_guess="轻小说测试", series_key="轻小说测试", chapter_name="第1章", chapter_title="开始"), floor_rows=floor_rows, settings=_settings())
    second = build_rag_chunks(thread_row=_thread_row(tid=2, display_title="轻小说测试", raw_title="轻小说测试", forum_id=55, content_kind="novel", series_id=9), title_row=_title_row(core_title_guess="轻小说测试", series_key="轻小说测试", chapter_name="第1章", chapter_title="开始"), floor_rows=floor_rows, settings=_settings())

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert all(chunk.source_hash == first[0].source_hash for chunk in first)
    assert all(chunk.generated_at == first[0].generated_at for chunk in first)
    assert all(chunk.source_tid == 2 for chunk in first)
    assert all(chunk.cleaner_version == "anime-cleaner-1.2" for chunk in first)
    assert all(chunk.chunker_version == "anime-chunker-1.2" for chunk in first)
    assert all(chunk.materializer_version == "anime-rag-materializer-1.2" for chunk in first)


def test_chunker_uses_shared_cleaner_output_on_floor_chunks():
    floor_rows = [
        {
            "pid": 33,
            "floor_no": 1,
            "publisher": "lz",
            "pub_time": "2026-01-01",
            "content": "这是一段很长的非小说正文。" * 20 + "\n\n" + "第二段内容。" * 20,
            "quote_text": None,
            "reply_text": None,
        }
    ]

    chunks = build_rag_chunks(
        thread_row=_thread_row(tid=3, display_title="漫画测试", raw_title="漫画测试", forum_id=30, content_kind="comic", series_id=9),
        title_row=_title_row(core_title_guess="漫画测试", series_key="漫画测试", chapter_name="第1话", chapter_title="开始"),
        floor_rows=floor_rows,
        settings=_settings(),
    )

    floor_chunks = [chunk for chunk in chunks if chunk.floor_no == 1]
    assert floor_chunks
    assert all(chunk.source_tid == 3 for chunk in floor_chunks)
    assert all(chunk.source_pid == 33 for chunk in floor_chunks)
    assert all(chunk.source_floor_no == 1 for chunk in floor_chunks)
    assert all(chunk.source_hash for chunk in floor_chunks)
    assert all(chunk.generated_at for chunk in floor_chunks)
    assert all(chunk.cleaner_version == "anime-cleaner-1.2" for chunk in floor_chunks)
    assert all(chunk.chunker_version == "anime-chunker-1.2" for chunk in floor_chunks)
    assert all(chunk.materializer_version == "anime-rag-materializer-1.2" for chunk in floor_chunks)
