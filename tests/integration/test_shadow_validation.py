from __future__ import annotations

import json
import runpy
import struct
from dataclasses import replace
from pathlib import Path

from sqlalchemy import text

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.repositories.rag_vectors import get_vector_repository


def _vector(dimensions: int = 512) -> list[float]:
    return [0.1, 0.2, 0.3] + [0.0] * (dimensions - 3)


def test_validate_shadow_readonly_smoke(tmp_path, pg_engine):
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "validate_shadow_readonly.py"
    script = runpy.run_path(str(script_path))
    validate_shadow_readonly = script["validate_shadow_readonly"]

    sqlite_db_path = tmp_path / "shadow.sqlite3"
    sqlite_settings = replace(load_settings(), db_backend="sqlite", db_path=sqlite_db_path, db_url=None)
    pg_schema = "shadow_smoke"
    postgres_settings = replace(
        load_settings(),
        db_backend="postgres",
        db_url=pg_engine.url.render_as_string(hide_password=False),
        db_schema=pg_schema,
    )

    sqlite_conn = connect(sqlite_settings)
    pg_conn = connect(postgres_settings)
    try:
        for conn in (sqlite_conn, pg_conn):
            if conn.backend == "postgres":
                conn.execute(text(f'SET search_path TO "{pg_schema}", public'))
            conn.execute(
                """
                INSERT INTO threads (
                  tid, page_type, raw_title, display_title, publisher, pub_time, sync_time,
                  archive_status, validation_status, forum_id, content_kind, primary_media_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    501,
                    "thread_detail",
                    "影子验证标题",
                    "影子验证标题",
                    "作者",
                    "2026-01-01T00:00:00+08:00",
                    "2026-01-01T01:00:00+08:00",
                    "complete",
                    "valid",
                    55,
                    "novel",
                    "text",
                ),
            )
            conn.execute(
                """
                INSERT INTO title_parse (
                  tid, raw_title, display_title, group_name, author_guess, core_title_guess,
                  normalized_core_title, series_key, parser_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (501, "影子验证标题", "影子验证标题", "作者", None, "影子验证标题", "影子验证标题", "shadow-series", "title-v1"),
            )
            conn.execute(
                """
                INSERT INTO rag_chunks (
                  id, chunk_id, tid, pid, floor_no, chunk_type, forum_id, content_kind, series_id,
                  series_key, chapter_index, publisher, pub_time, title, metadata_text, text,
                  text_hash, source_uri, embedding_model, embedding_dimensions, embedding_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    601,
                    "thread:501:title",
                    501,
                    None,
                    None,
                    "thread_title",
                    55,
                    "novel",
                    None,
                    "shadow-series",
                    None,
                    "作者",
                    "2026-01-01T00:00:00+08:00",
                    "影子验证标题",
                    "作者",
                    "影子验证标题",
                    "hash-1",
                    "yamibo://threads/501/summary",
                    "text-embedding-3-small",
                    512,
                    "pending",
                ),
            )
            if conn.backend == "postgres":
                get_vector_repository(conn).replace_embeddings([(601, _vector())])
            conn.commit()

        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(
            json.dumps(
                {
                    "thread_search": [
                        {"query": "影子验证标题", "top_k": 1, "expected_tids": [501], "expected_titles": ["影子验证标题"]}
                    ],
                    "rag_vector_search": [
                        {
                            "query": "影子验证标题",
                            "top_k": 1,
                            "query_embedding": _vector(),
                            "expected": [{"chunk_id": "thread:501:title", "distance": 0.0}],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        report = validate_shadow_readonly(
            sqlite_settings=sqlite_settings,
            postgres_settings=postgres_settings,
            baseline_path=baseline_path,
        )

        assert report["ok"] is True
        assert report["count_mismatches"] == []
        assert report["thread_search_mismatches"] == []
        assert report["vector_search_mismatches"] == []
    finally:
        sqlite_conn.close()
        pg_conn.close()
        with pg_engine.begin() as raw_conn:
            raw_conn.execute(text(f'DROP SCHEMA IF EXISTS "{pg_schema}" CASCADE'))
