from __future__ import annotations

import json
import runpy
import struct
import sqlite3
from pathlib import Path

from sqlalchemy import text

from yamibo_mcp.db.connection import DatabaseConnection, connect
from yamibo_mcp.db.repositories.rag_vectors import get_vector_repository
from yamibo_mcp.db.repositories.threads import ThreadsRepository


def _load_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_sqlite_to_postgres.py"
    return runpy.run_path(str(script_path))


def _make_vector_blob(dimensions: int = 512) -> bytes:
    vector = [0.1, 0.2, 0.3] + [0.0] * (dimensions - 3)
    return struct.pack(f"{dimensions}f", *vector)


def test_sqlite_to_postgres_etl_smoke(tmp_path, pg_engine, monkeypatch):
    monkeypatch.setenv("YAMIBO_DB_BACKEND", "sqlite")
    monkeypatch.delenv("YAMIBO_DB_URL", raising=False)

    source_db_path = tmp_path / "source.db"
    source = connect(source_db_path)
    try:
        source.execute(
            """
            INSERT INTO series (series_id, canonical_title, normalized_title, series_key, needs_review)
            VALUES (?, ?, ?, ?, ?)
            """,
            (1, "测试系列", "测试系列", "test-series", 0),
        )
        source.execute(
            """
            INSERT INTO jobs (job_id, job_type, status, payload_json, artifacts_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("job_1", "archive_thread", "queued", json.dumps({"tid": 1001}, ensure_ascii=False), "{}"),
        )
        source.execute(
            """
            INSERT INTO job_events (job_id, event_type, status, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            ("job_1", "job.created", "queued", "{}"),
        )
        source.execute(
            """
            INSERT INTO threads (
              tid, series_id, page_type, raw_title, display_title, publisher, pub_time, sync_time,
              archive_status, validation_status, forum_id, content_kind, primary_media_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1001,
                1,
                "thread_detail",
                "测试轻小说 13话",
                "测试轻小说 13话",
                "汉化工房九九组",
                "2026-01-01T00:00:00+08:00",
                "2026-01-01T01:00:00+08:00",
                "complete",
                "valid",
                55,
                "novel",
                "text",
            ),
        )
        source.execute(
            """
            INSERT INTO title_parse (
              tid, raw_title, display_title, group_name, author_guess, core_title_guess,
              normalized_core_title, series_key, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1001,
                "测试轻小说 13话",
                "测试轻小说 13话",
                "汉化工房九九组",
                None,
                "测试轻小说",
                "测试轻小说",
                "test-series",
                "title-v1",
            ),
        )
        source.execute(
            """
            INSERT INTO floors (pid, tid, floor_no, publisher, content, pub_time, has_images)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (2001, 1001, 1, "汉化工房九九组", "第一楼正文", "2026-01-01T01:00:00+08:00", 0),
        )
        source.execute(
            """
            INSERT INTO rag_chunks (
              id, chunk_id, tid, pid, floor_no, chunk_type, forum_id, content_kind, series_id,
              series_key, chapter_index, publisher, pub_time, title, metadata_text, text,
              text_hash, source_uri, embedding_model, embedding_dimensions, embedding_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3001,
                "thread:1001:title",
                1001,
                None,
                None,
                "thread_title",
                55,
                "novel",
                1,
                "test-series",
                None,
                "汉化工房九九组",
                "2026-01-01T00:00:00+08:00",
                "测试轻小说 13话",
                "测试轻小说",
                "测试轻小说 13话",
                "hash-1",
                "yamibo://threads/1001/summary",
                "text-embedding-3-small",
                512,
                "pending",
            ),
        )
        source.execute("CREATE TABLE rag_chunk_vec (embedding BLOB NOT NULL)")
        source.execute(
            "INSERT INTO rag_chunk_vec(rowid, embedding) VALUES (?, ?)",
            (3001, sqlite3.Binary(_make_vector_blob())),
        )
        source.commit()
    finally:
        source.close()

    script = _load_script()
    schema = "etl_smoke"
    try:
        report = script["migrate_sqlite_to_postgres"](
            source_db=source_db_path,
            target_db_url=pg_engine.url.render_as_string(hide_password=False),
            schema=schema,
        )

        assert report["row_counts"]["series"] == 1
        assert report["row_counts"]["jobs"] == 1
        assert report["row_counts"]["job_events"] == 1
        assert report["row_counts"]["threads"] == 1
        assert report["row_counts"]["rag_chunks"] == 1
        assert report["copied_rows"]["rag_chunk_vec"] == 1

        with pg_engine.connect() as raw_conn:
            raw_conn.execute(text(f'SET search_path TO "{schema}", public'))
            conn = DatabaseConnection(raw_conn, backend="postgres")
            threads = ThreadsRepository(conn).search_threads("测试轻小说", limit=5)
            vectors = get_vector_repository(conn).search(query_embedding=[0.1, 0.2, 0.3] + [0.0] * 509, top_k=5)

            assert threads[0]["tid"] == 1001
            assert vectors[0]["chunk_id"] == "thread:1001:title"
            assert vectors[0]["vector_distance"] == 0.0
    finally:
        with pg_engine.begin() as raw_conn:
            raw_conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
