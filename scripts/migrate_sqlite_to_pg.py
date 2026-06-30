"""
One-shot migration: SQLite → PostgreSQL.

Merges records that exist in SQLite but not in PostgreSQL.
Existing PG records are left untouched (PG already has more threads/floors/content).
"""
from __future__ import annotations

import sqlite3
import struct
import sys
from pathlib import Path

import psycopg

SQLITE_PATH = Path("/Users/vv/Code/Yamibo/data/forum.db")
PG_URL = "postgresql://yamibo:yamibo@127.0.0.1:5432/yamibo"

BATCH_SIZE = 5000


def migrate_jobs(sql: sqlite3.Connection, pg) -> int:
    """Copy SQLite-only jobs into PG. PK = job_id."""
    existing = set()
    with pg.cursor() as cur:
        cur.execute("SELECT job_id FROM jobs")
        existing.update(r[0] for r in cur)

    rows = list(sql.execute(
        "SELECT job_id, parent_job_id, job_type, tid, payload_json, status, stage, "
        "progress_current, progress_total, worker_id, heartbeat_at, lease_until, "
        "retry_count, max_retries, resumable, cancel_requested_at, paused_at, "
        "error_code, error_message, artifacts_json, created_at, updated_at, finished_at "
        "FROM jobs ORDER BY created_at"
    ))
    to_insert = [r for r in rows if r[0] not in existing]
    # SQLite stores resumable as int (0/1); PG expects bool.
    to_insert = [r[:14] + (bool(r[14]),) + r[15:] for r in to_insert]
    if not to_insert:
        print(f"  jobs: all {len(rows)} already in PG (PG has {len(existing)})")
        return 0

    with pg.cursor() as cur:
        cur.executemany("""
            INSERT INTO jobs (job_id, parent_job_id, job_type, tid, payload_json, status, stage,
              progress_current, progress_total, worker_id, heartbeat_at, lease_until,
              retry_count, max_retries, resumable, cancel_requested_at, paused_at,
              error_code, error_message, artifacts_json, created_at, updated_at, finished_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s,
              %s, %s, %s, %s::timestamptz, %s::timestamptz,
              %s, %s, %s::boolean, %s::timestamptz, %s::timestamptz,
              %s, %s, %s::jsonb, %s::timestamptz, %s::timestamptz, %s::timestamptz)
            ON CONFLICT (job_id) DO NOTHING
        """, to_insert)
    pg.commit()
    print(f"  jobs: {len(to_insert)} inserted (SQLite {len(rows)}, PG {len(existing)})")
    return len(to_insert)


def migrate_job_events(sql: sqlite3.Connection, pg) -> int:
    """Copy SQLite-only job_events. Dedup by job_id + event_type + created_at."""
    existing = set()
    with pg.cursor() as cur:
        cur.execute("SELECT job_id, event_type, created_at FROM job_events")
        existing.update((r[0], r[1], r[2]) for r in cur)

    rows = list(sql.execute(
        "SELECT job_id, event_type, status, stage, payload_json, created_at "
        "FROM job_events ORDER BY event_id"
    ))
    to_insert = [r for r in rows if (r[0], r[1], r[5]) not in existing]

    # Filter out events whose job_id doesn't exist in PG (orphaned events)
    with pg.cursor() as cur:
        cur.execute("SELECT job_id FROM jobs")
        pg_job_ids = set(r[0] for r in cur)
    to_insert = [r for r in to_insert if r[0] in pg_job_ids]

    if not to_insert:
        print(f"  job_events: all {len(rows)} already in PG (PG has {len(existing)})")
        return 0

    with pg.cursor() as cur:
        cur.executemany("""
            INSERT INTO job_events (job_id, event_type, status, stage, payload_json, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::timestamptz)
        """, to_insert)
    pg.commit()
    print(f"  job_events: {len(to_insert)} inserted (SQLite {len(rows)}, PG {len(existing)})")
    return len(to_insert)


def migrate_rag_chunks(sql: sqlite3.Connection, pg) -> int:
    """Copy SQLite-only rag_chunks by chunk_id."""
    existing = set()
    with pg.cursor() as cur:
        cur.execute("SELECT chunk_id FROM rag_chunks")
        existing.update(r[0] for r in cur)

    rows = list(sql.execute(
        "SELECT chunk_id, tid, pid, floor_no, chunk_type, forum_id, content_kind, "
        "series_id, series_key, chapter_index, publisher, pub_time, title, "
        "metadata_text, text, text_hash, source_uri, "
        "embedding_model, embedding_dimensions, embedding_status, indexed_at, updated_at "
        "FROM rag_chunks ORDER BY id"
    ))
    to_insert = [r for r in rows if r[0] not in existing]
    if not to_insert:
        print(f"  rag_chunks: all {len(rows)} already in PG (PG has {len(existing)})")
        return 0

    with pg.cursor() as cur:
        cur.executemany("""
            INSERT INTO rag_chunks (chunk_id, tid, pid, floor_no, chunk_type, forum_id,
              content_kind, series_id, series_key, chapter_index, publisher, pub_time,
              title, metadata_text, text, text_hash, source_uri,
              embedding_model, embedding_dimensions, embedding_status, indexed_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s::timestamptz,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s::timestamptz, %s::timestamptz)
            ON CONFLICT (chunk_id) DO NOTHING
        """, to_insert)
    pg.commit()
    print(f"  rag_chunks: {len(to_insert)} inserted (SQLite {len(rows)}, PG {len(existing)})")
    return len(to_insert)


def migrate_rag_vectors(sql: sqlite3.Connection, pg) -> int:
    """Extract vectors from sqlite-vec via vec_to_json, update PG rag_chunks.embedding."""
    try:
        sql.enable_load_extension(True)
        # Find the sqlite-vec extension in the venv
        import subprocess
        result = subprocess.run(
            ["find", ".venv", "-name", "vec0.dylib", "-type", "f"],
            capture_output=True, text=True, cwd="/Users/vv/Code/Yamibo"
        )
        vec_path = result.stdout.strip().split("\n")[0]
        sql.load_extension(f"/Users/vv/Code/Yamibo/{vec_path}")
    except Exception as e:
        print(f"  rag_vectors: skipped (cannot load vec0: {e})")
        return 0

    try:
        vec_rows = list(sql.execute(
            "SELECT v.rowid, vec_to_json(v.embedding) FROM rag_chunk_vec v "
            "JOIN rag_chunks c ON c.id = v.rowid "
            "WHERE c.embedding_status = 'indexed'"
        ))
    except Exception as e:
        print(f"  rag_vectors: skipped (query error: {e})")
        return 0

    if not vec_rows:
        print("  rag_vectors: no completed vectors in SQLite")
        return 0

    id_to_chunk = {}
    for r in sql.execute("SELECT id, chunk_id FROM rag_chunks WHERE embedding_status = 'indexed'"):
        id_to_chunk[r[0]] = r[1]

    with pg.cursor() as cur:
        cur.execute("SELECT chunk_id FROM rag_chunks WHERE embedding_status != 'indexed' OR embedding IS NULL")
        needs_embedding = set(r[0] for r in cur)

    updated = 0
    with pg.cursor() as cur:
        for rowid, vec_json in vec_rows:
            chunk_id = id_to_chunk.get(rowid)
            if not chunk_id or chunk_id not in needs_embedding:
                continue
            if not vec_json:
                continue
            cur.execute(
                "UPDATE rag_chunks SET embedding = %s::vector, embedding_status = 'completed' WHERE chunk_id = %s",
                (vec_json, chunk_id)
            )
            updated += 1
    pg.commit()
    print(f"  rag_vectors: {updated} embeddings updated")
    return updated


def main():
    if not SQLITE_PATH.exists():
        print(f"SQLite file not found: {SQLITE_PATH}")
        sys.exit(1)

    sql = sqlite3.connect(str(SQLITE_PATH))
    sql.row_factory = sqlite3.Row

    pg = psycopg.connect(PG_URL)
    try:
        print("Migrating SQLite → PostgreSQL...\n")

        n = migrate_jobs(sql, pg)
        n += migrate_job_events(sql, pg)
        n += migrate_rag_chunks(sql, pg)
        n += migrate_rag_vectors(sql, pg)

        print(f"\nDone. {n} total records migrated.")
    finally:
        sql.close()
        pg.close()


if __name__ == "__main__":
    main()
