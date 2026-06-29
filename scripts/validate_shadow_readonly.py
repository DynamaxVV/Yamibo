from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import text

from yamibo_mcp.config import Settings, load_settings
from yamibo_mcp.db.connection import DatabaseConnection, connect
from yamibo_mcp.db.repositories.rag_vectors import get_vector_repository
from yamibo_mcp.db.repositories.rag_vectors import RagVectorUnavailableError
from yamibo_mcp.db.repositories.threads import ThreadsRepository


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _thread_signature(row: Any) -> dict[str, Any]:
    return {
        "tid": int(row["tid"]),
        "title": row["display_title"] or row["raw_title"],
    }


def _vector_signature(row: Any) -> dict[str, Any]:
    return {
        "chunk_id": row["chunk_id"],
        "distance": float(row["vector_distance"]),
    }


def _thread_search_signature(repo, query: str, *, top_k: int, forum_id: int | None = None) -> list[dict[str, Any]]:
    rows = repo.search_threads(query, limit=top_k, forum_id=forum_id)
    return [_thread_signature(row) for row in rows[:top_k]]


def _vector_search_signature(repo, query_embedding: list[float], *, top_k: int) -> list[dict[str, Any]]:
    rows = repo.search(query_embedding=query_embedding, top_k=top_k)
    return [_vector_signature(row) for row in rows[:top_k]]


def _load_query_embedding_from_source(sqlite_conn: DatabaseConnection, entry: dict[str, Any]) -> list[float] | None:
    expected = entry.get("expected") or []
    if not expected:
        return None
    chunk_id = expected[0].get("chunk_id")
    if not chunk_id:
        return None
    try:
        import sqlite_vec

        sqlite_conn.enable_load_extension(True)
        sqlite_conn.load_extension(sqlite_vec.loadable_path())
        row = sqlite_conn.execute(
            """
            SELECT v.embedding
            FROM rag_chunk_vec v
            JOIN rag_chunks c ON c.id = v.rowid
            WHERE c.chunk_id = ?
            LIMIT 1
            """,
            (chunk_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        try:
            sqlite_conn.enable_load_extension(False)
        except Exception:
            pass
    if row is None:
        return None
    embedding = row["embedding"]
    if embedding is None:
        return None
    if isinstance(embedding, bytes):
        import struct

        return list(struct.unpack(f"{len(embedding) // 4}f", embedding))
    if isinstance(embedding, memoryview):
        import struct

        raw = embedding.tobytes()
        return list(struct.unpack(f"{len(raw) // 4}f", raw))
    if isinstance(embedding, list):
        return [float(item) for item in embedding]
    return [float(item) for item in embedding]


def _vector_search_signature_safe(
    repo,
    query_embedding: list[float],
    *,
    top_k: int,
    backend_name: str,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        return _vector_search_signature(repo, query_embedding, top_k=top_k), None
    except (RagVectorUnavailableError, Exception) as exc:
        return None, f"{backend_name}: {exc}"


def _compare_counts(sqlite_conn: DatabaseConnection, pg_conn: DatabaseConnection) -> list[dict[str, Any]]:
    tables = ("forums", "series", "threads", "floors", "jobs", "job_events", "rag_chunks")
    mismatches: list[dict[str, Any]] = []
    for table in tables:
        left = int(sqlite_conn.execute(text(f'SELECT COUNT(*) AS c FROM "{table}"')).scalar_one())
        right = int(pg_conn.execute(text(f'SELECT COUNT(*) AS c FROM "{table}"')).scalar_one())
        if left != right:
            mismatches.append({"table": table, "sqlite": left, "postgres": right})
    return mismatches


def validate_shadow_readonly(
    *,
    sqlite_settings: Settings,
    postgres_settings: Settings,
    baseline_path: Path | None = None,
) -> dict[str, Any]:
    sqlite_conn = connect(sqlite_settings)
    pg_conn = connect(postgres_settings)
    try:
        if postgres_settings.db_schema:
            pg_conn.execute(text(f'SET search_path TO "{postgres_settings.db_schema}", public'))
        sqlite_repo = ThreadsRepository(sqlite_conn)
        pg_repo = ThreadsRepository(pg_conn)
        sqlite_vectors = get_vector_repository(sqlite_conn)
        pg_vectors = get_vector_repository(pg_conn)

        thread_queries: list[dict[str, Any]] = []
        vector_queries: list[dict[str, Any]] = []
        if baseline_path is not None:
            baseline = _load_json(baseline_path)
            thread_queries = list(baseline.get("thread_search", []))
            vector_queries = list(baseline.get("rag_vector_search", []))

        thread_mismatches: list[dict[str, Any]] = []
        thread_exact_matches = 0
        for entry in thread_queries:
            top_k = int(entry["top_k"])
            source_sig = _thread_search_signature(sqlite_repo, entry["query"], top_k=top_k)
            target_sig = _thread_search_signature(pg_repo, entry["query"], top_k=top_k)
            if source_sig == target_sig:
                thread_exact_matches += 1
                continue
            thread_mismatches.append(
                {
                    "query": entry["query"],
                    "source": source_sig,
                    "target": target_sig,
                }
            )

        vector_mismatches: list[dict[str, Any]] = []
        vector_exact_matches = 0
        sqlite_vector_skip: str | None = None
        pg_vector_skip: str | None = None
        for entry in vector_queries:
            query_embedding = entry.get("query_embedding")
            if not query_embedding:
                query_embedding = _load_query_embedding_from_source(sqlite_conn, entry)
            if not query_embedding:
                continue
            top_k = int(entry["top_k"])
            source_sig, sqlite_skip = _vector_search_signature_safe(
                sqlite_vectors, query_embedding, top_k=top_k, backend_name="sqlite"
            )
            target_sig, pg_skip = _vector_search_signature_safe(
                pg_vectors, query_embedding, top_k=top_k, backend_name="postgres"
            )
            sqlite_vector_skip = sqlite_vector_skip or sqlite_skip
            pg_vector_skip = pg_vector_skip or pg_skip
            if source_sig is None or target_sig is None:
                continue
            if source_sig == target_sig:
                vector_exact_matches += 1
                continue
            vector_mismatches.append(
                {
                    "query": entry["query"],
                    "source": source_sig,
                    "target": target_sig,
                }
            )

        report = {
            "thread_search_mismatches": thread_mismatches,
            "thread_search_exact_match_rate": (thread_exact_matches / len(thread_queries)) if thread_queries else None,
            "vector_search_mismatches": vector_mismatches,
            "vector_search_exact_match_rate": (vector_exact_matches / len(vector_queries)) if vector_queries else None,
            "vector_search_skipped": [item for item in (sqlite_vector_skip, pg_vector_skip) if item is not None],
            "count_mismatches": _compare_counts(sqlite_conn, pg_conn),
        }
        report["ok"] = not report["thread_search_mismatches"] and not report["vector_search_mismatches"] and not report["count_mismatches"]
        return report
    finally:
        sqlite_conn.close()
        pg_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare SQLite and PostgreSQL read-only results.")
    parser.add_argument("--sqlite-db", help="SQLite source database path.")
    parser.add_argument("--postgres-db-url", help="PostgreSQL target database URL.")
    parser.add_argument("--schema", help="PostgreSQL schema.")
    parser.add_argument("--baseline", help="Optional JSON baseline file.")
    parser.add_argument("--output-json", help="Write the comparison report to a file.")
    args = parser.parse_args()

    settings = load_settings()
    sqlite_settings = replace(
        settings,
        db_backend="sqlite",
        db_path=Path(args.sqlite_db).expanduser() if args.sqlite_db else settings.db_path,
        db_url=None,
    )
    postgres_settings = replace(
        settings,
        db_backend="postgres",
        db_url=args.postgres_db_url or settings.db_url,
        db_schema=args.schema or settings.db_schema,
    )
    if not postgres_settings.db_url:
        raise SystemExit("PostgreSQL URL is required")

    baseline_path = Path(args.baseline).expanduser() if args.baseline else None
    report = validate_shadow_readonly(
        sqlite_settings=sqlite_settings,
        postgres_settings=postgres_settings,
        baseline_path=baseline_path,
    )
    report_line = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output_json:
        Path(args.output_json).expanduser().write_text(report_line + "\n", encoding="utf-8")
    print(report_line)


if __name__ == "__main__":
    main()
