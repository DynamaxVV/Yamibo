from __future__ import annotations

import sqlite3

import pytest

from yamibo_mcp.db.repositories.rag_vectors import get_vector_repository


pytest.importorskip("sqlite_vec")


def test_vector_search_uses_k_constraint_required_by_sqlite_vec():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE rag_chunks (
          id INTEGER PRIMARY KEY,
          tid INTEGER,
          pid INTEGER,
          floor_no INTEGER,
          forum_id INTEGER,
          content_kind TEXT,
          series_id INTEGER,
          chunk_id TEXT,
          title TEXT,
          publisher TEXT,
          pub_time TEXT,
          text TEXT,
          source_uri TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO rag_chunks (
          id, tid, pid, floor_no, forum_id, content_kind, series_id, chunk_id, title, publisher, pub_time, text, source_uri
        ) VALUES (
          1, 7001, 7010, 1, 55, 'novel', 1, 'thread:7001:floor:1:part:1', '测试轻小说', '作者', '2026-01-01', '少女在星空下告白。', 'yamibo://threads/7001/posts#floor=1'
        )
        """
    )

    repo = get_vector_repository(conn)
    repo.ensure_schema(dimensions=3)
    repo.replace_embeddings([(1, [0.1, 0.2, 0.3])])

    rows = repo.search(query_embedding=[0.1, 0.2, 0.3], top_k=5)

    assert len(rows) == 1
    assert rows[0]["chunk_id"] == "thread:7001:floor:1:part:1"
    assert rows[0]["vector_distance"] == 0.0
