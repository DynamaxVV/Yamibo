from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yamibo_mcp.application.rag_queries import search_archived_content
from yamibo_mcp.db.repositories.rag_chunks import RagChunksRepository
from yamibo_mcp.rag.chunker import RagChunk


def _settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        db_path=str(tmp_path / "test.db"),
        rag_enabled=True,
        rag_base_url="https://embeddings.example.invalid/v1",
        rag_api_key="rag-test-key",
        rag_embedding_model="text-embedding-3-small",
        rag_embedding_dimensions=512,
        rag_hybrid_fts_candidates=20,
        rag_hybrid_vector_candidates=20,
        rag_embedding_provider="openai",
        llm_api_key="test-key",
        llm_base_url="https://example.invalid/v1",
    )


def _seed_thread_and_chunks(db) -> None:
    db.execute(
        """
        INSERT INTO threads (
          tid, page_type, raw_title, display_title, archive_status, validation_status,
          forum_id, content_kind, primary_media_type, publisher, pub_time
        ) VALUES (7001, 'thread_detail', '测试轻小说', '测试轻小说', 'complete', 'valid', 55, 'novel', 'text', '作者', '2026-01-01')
        """
    )
    repo = RagChunksRepository(db)
    repo.replace_thread_chunks(
        tid=7001,
        chunks=[
            RagChunk(
                chunk_id="thread:7001:title",
                tid=7001,
                pid=None,
                floor_no=None,
                chunk_type="thread_title",
                forum_id=55,
                content_kind="novel",
                series_id=1,
                series_key="测试轻小说",
                chapter_index=1.0,
                publisher="作者",
                pub_time="2026-01-01",
                title="测试轻小说",
                metadata_text="作者\n测试轻小说",
                text="测试轻小说 第一章",
                text_hash="hash-a",
                source_uri="yamibo://threads/7001/summary",
            ),
            RagChunk(
                chunk_id="thread:7001:floor:1:part:1",
                tid=7001,
                pid=7010,
                floor_no=1,
                chunk_type="floor",
                forum_id=55,
                content_kind="novel",
                series_id=1,
                series_key="测试轻小说",
                chapter_index=1.0,
                publisher="作者",
                pub_time="2026-01-01",
                title="测试轻小说",
                metadata_text="作者\n测试轻小说",
                text="少女在星空下告白，后来两人一起出发。",
                text_hash="hash-b",
                source_uri="yamibo://threads/7001/posts#floor=1",
            ),
        ],
        embedding_model="text-embedding-3-small",
        embedding_dimensions=512,
    )


def test_search_archived_content_keyword_returns_local_evidence(tmp_path, db):
    settings = _settings(tmp_path)
    _seed_thread_and_chunks(db)

    with patch("yamibo_mcp.application.rag_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.rag_queries.connect", return_value=db):
        result = search_archived_content(query="星空 告白", mode="keyword", top_k=5)

    assert result.ok is True
    assert result.data["count"] >= 1
    item = result.data["items"][0]
    assert item["tid"] == 7001
    assert item["source_uri"].startswith("yamibo://threads/7001/")
    assert item["score_parts"]["keyword"] > 0


def test_search_archived_content_snippet_centers_match(tmp_path, db):
    settings = _settings(tmp_path)
    db.execute(
        """
        INSERT INTO threads (
          tid, page_type, raw_title, display_title, archive_status, validation_status,
          forum_id, content_kind, primary_media_type, publisher, pub_time
        ) VALUES (7002, 'thread_detail', '长文本帖子', '长文本帖子', 'complete', 'valid', 55, 'novel', 'text', '作者', '2026-01-01')
        """
    )
    repo = RagChunksRepository(db)
    repo.replace_thread_chunks(
        tid=7002,
        chunks=[
            RagChunk(
                chunk_id="thread:7002:floor:1:part:1",
                tid=7002,
                pid=7020,
                floor_no=1,
                chunk_type="floor",
                forum_id=55,
                content_kind="novel",
                series_id=1,
                series_key="长文本帖子",
                chapter_index=1.0,
                publisher="作者",
                pub_time="2026-01-01",
                title="长文本帖子",
                metadata_text="作者\n长文本帖子",
                text="前言内容" + "无关描述" * 40 + "星空下告白" + "后文继续" * 20,
                text_hash="hash-c",
                source_uri="yamibo://threads/7002/posts#floor=1",
            ),
        ],
        embedding_model="text-embedding-3-small",
        embedding_dimensions=512,
    )

    with patch("yamibo_mcp.application.rag_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.rag_queries.connect", return_value=db):
        result = search_archived_content(query="星空下告白", mode="keyword", top_k=5)

    assert result.ok is True
    snippet = result.data["items"][0]["snippet"]
    assert "星空下告白" in snippet
    assert snippet.startswith("...") or snippet.endswith("...")


def test_search_archived_content_vector_uses_fake_provider(tmp_path, db):
    settings = _settings(tmp_path)
    _seed_thread_and_chunks(db)

    class FakeProvider:
        model = "text-embedding-3-small"
        dimensions = 512

        def embed_texts(self, texts):
            assert texts == ["语义查询"]
            return [[0.1, 0.2, 0.3]]

    class FakeVectorsRepo:
        def __init__(self, conn):
            self.conn = conn

        def search(self, **kwargs):
            assert kwargs["query_embedding"] == [0.1, 0.2, 0.3]
            return db.execute("SELECT *, 0.05 AS vector_distance FROM rag_chunks WHERE chunk_id = 'thread:7001:floor:1:part:1'").fetchall()

    with patch("yamibo_mcp.application.rag_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.rag_queries.connect", return_value=db), \
         patch("yamibo_mcp.application.rag_queries.build_embedding_provider", return_value=FakeProvider()), \
         patch("yamibo_mcp.application.rag_queries.RagVectorsRepository", FakeVectorsRepo):
        result = search_archived_content(query="语义查询", mode="vector", top_k=5)

    assert result.ok is True
    assert result.data["items"][0]["chunk_id"] == "thread:7001:floor:1:part:1"
    assert result.data["items"][0]["score_parts"]["vector"] > 0


def test_search_archived_content_does_not_fetch_remote(tmp_path, db):
    settings = _settings(tmp_path)
    _seed_thread_and_chunks(db)

    import yamibo_mcp.application.rag_queries as rag_queries

    with patch("yamibo_mcp.application.rag_queries.load_settings", return_value=settings), \
         patch("yamibo_mcp.application.rag_queries.connect", return_value=db):
        result = search_archived_content(query="测试", mode="keyword", top_k=3)

    assert result.ok is True
    assert not hasattr(rag_queries, "YamiboClient")
