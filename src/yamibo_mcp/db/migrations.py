from __future__ import annotations

import argparse
import sqlite3
from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.alembic_runner import upgrade_postgres_schema


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  parent_job_id TEXT,
  job_type TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  tid INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  stage TEXT,
  progress_current INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER,
  worker_id TEXT,
  lease_token TEXT,
  heartbeat_at TEXT,
  lease_until TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 3,
  resumable INTEGER NOT NULL DEFAULT 1,
  cancel_requested_at TEXT,
  paused_at TEXT,
  started_at TEXT,
  error_code TEXT,
  error_message TEXT,
  artifacts_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_lease ON jobs(status, lease_until);
CREATE INDEX IF NOT EXISTS idx_jobs_tid_type ON jobs(tid, job_type);
CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at);
CREATE INDEX IF NOT EXISTS idx_jobs_parent_created ON jobs(parent_job_id, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS series (
  series_id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  series_key TEXT NOT NULL UNIQUE,
  alias_keys_json TEXT,
  aliases_json TEXT,
  author_guess TEXT,
  creator_key TEXT,
  merge_confidence REAL,
  needs_review INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS threads (
  tid INTEGER PRIMARY KEY,
  series_id INTEGER REFERENCES series(series_id),
  page_type TEXT NOT NULL DEFAULT 'unknown',
  raw_title TEXT NOT NULL,
  display_title TEXT,
  publisher TEXT,
  publisher_uid TEXT,
  pub_time TEXT,
  sync_time TEXT,
  last_pid INTEGER,
  local_reply_count INTEGER,
  reply_count_checked_at TEXT,
  reply_count_mismatch_reason TEXT,
  remote_last_reply_at_raw TEXT,
  remote_last_reply_at TEXT,
  remote_last_replier TEXT,
  remote_reply_count INTEGER,
  remote_observed_at TEXT,
  remote_observed_from TEXT,
  permission INTEGER NOT NULL DEFAULT 0 CHECK(permission >= 0),
  is_finished INTEGER NOT NULL DEFAULT 0,
  image_count INTEGER NOT NULL DEFAULT 0 CHECK(image_count >= 0),
  context_path TEXT,
  archive_status TEXT NOT NULL DEFAULT 'stale',
  capture_mode TEXT NOT NULL DEFAULT 'full' CHECK(capture_mode IN ('text_only', 'full')),
  validation_status TEXT NOT NULL DEFAULT 'unknown',
  validation_errors_json TEXT,
  missing_images_json TEXT,
  needs_title_review INTEGER NOT NULL DEFAULT 0,
  needs_series_review INTEGER NOT NULL DEFAULT 0,
  is_exported INTEGER NOT NULL DEFAULT 0,
  export_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_threads_series_id ON threads(series_id);
CREATE INDEX IF NOT EXISTS idx_threads_archive_status ON threads(archive_status);
CREATE INDEX IF NOT EXISTS idx_threads_sync_time ON threads(sync_time);

CREATE TABLE IF NOT EXISTS forums (
  forum_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  content_kind TEXT NOT NULL,
  base_url TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_blocks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tid INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  order_index INTEGER NOT NULL,
  block_type TEXT NOT NULL,
  text TEXT,
  asset_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_content_blocks_tid_order ON content_blocks(tid, order_index);

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  tid INTEGER NOT NULL,
  pid INTEGER NOT NULL,
  asset_type TEXT NOT NULL,
  remote_url TEXT NOT NULL,
  local_path TEXT,
  exportable INTEGER NOT NULL DEFAULT 0,
  required INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_tid ON assets(tid);
CREATE INDEX IF NOT EXISTS idx_assets_tid_pid_type ON assets(tid, pid, asset_type);

CREATE TABLE IF NOT EXISTS job_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT,
  stage TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_id_event_id
ON job_events(job_id, event_id);

CREATE INDEX IF NOT EXISTS idx_job_events_created_at
ON job_events(created_at);

CREATE TABLE IF NOT EXISTS floors (
  pid INTEGER PRIMARY KEY,
  tid INTEGER NOT NULL REFERENCES threads(tid),
  floor_no INTEGER NOT NULL CHECK(floor_no > 0),
  publisher TEXT,
  publisher_uid TEXT,
  content TEXT,
  pub_time TEXT,
  has_images INTEGER NOT NULL DEFAULT 0,
  content_hash TEXT,
  quote_text TEXT,
  reply_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_floors_tid_floor ON floors(tid, floor_no);

CREATE TABLE IF NOT EXISTS catalog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tid INTEGER NOT NULL REFERENCES threads(tid),
  chapter_name TEXT,
  target_tid INTEGER,
  target_url TEXT,
  resolve_status TEXT NOT NULL DEFAULT 'resolved'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_unique
ON catalog(tid, target_tid, chapter_name);

CREATE TABLE IF NOT EXISTS title_parse (
  tid INTEGER PRIMARY KEY REFERENCES threads(tid),
  raw_title TEXT NOT NULL,
  display_title TEXT,
  group_name TEXT,
  author_guess TEXT,
  core_title_guess TEXT,
  normalized_core_title TEXT,
  series_key TEXT,
  title_aliases_json TEXT,
  chapter_name TEXT,
  chapter_index REAL,
  chapter_index_end REAL,
  chapter_title TEXT,
  subtitle TEXT,
  tags_json TEXT,
  confidence REAL,
  parser_version TEXT NOT NULL,
  needs_review INTEGER NOT NULL DEFAULT 0,
  warnings_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
  run_id TEXT PRIMARY KEY,
  tid INTEGER NOT NULL,
  mode TEXT NOT NULL,
  floors_added INTEGER NOT NULL DEFAULT 0,
  images_added INTEGER NOT NULL DEFAULT 0,
  pages_fetched INTEGER NOT NULL DEFAULT 0,
  validation_status TEXT NOT NULL,
  warnings_json TEXT,
  errors_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rag_index_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rag_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id TEXT NOT NULL UNIQUE,
  tid INTEGER NOT NULL REFERENCES threads(tid),
  pid INTEGER,
  floor_no INTEGER,
  chunk_type TEXT NOT NULL,
  forum_id INTEGER,
  content_kind TEXT,
  series_id INTEGER,
  series_key TEXT,
  chapter_index REAL,
  publisher TEXT,
  pub_time TEXT,
  title TEXT,
  metadata_text TEXT,
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  source_tid INTEGER,
  source_pid INTEGER,
  source_floor_no INTEGER,
  cleaner_version TEXT,
  chunker_version TEXT,
  materializer_version TEXT,
  source_hash TEXT,
  generated_at TEXT,
  quality_flags TEXT,
  embedding_model TEXT,
  embedding_dimensions INTEGER,
  embedding_status TEXT NOT NULL DEFAULT 'pending',
  indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_tid ON rag_chunks(tid);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_pid ON rag_chunks(pid);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_series ON rag_chunks(series_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_forum_kind ON rag_chunks(forum_id, content_kind);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding_status ON rag_chunks(embedding_status);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS thread_fts USING fts5(
  tid UNINDEXED,
  title,
  core_title,
  author,
  group_name,
  content_preview,
  catalog_text
);

CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
  chunk_id UNINDEXED,
  title,
  metadata_text,
  body
);
"""


def migrate(conn: Any, *, schema: str | None = None) -> None:
    backend = getattr(conn, "backend", None)
    if backend is None and hasattr(conn, "dialect"):
        backend = getattr(getattr(conn, "dialect", None), "name", None)
    if backend in {"postgres", "postgresql"}:
        upgrade_postgres_schema(conn, schema=schema or "public")
        return
    conn.executescript(SCHEMA_SQL)
    from yamibo_mcp.db.chat_schema import SCHEMA as CHAT_SCHEMA
    conn.executescript(CHAT_SCHEMA)
    conn.executescript(FTS_SQL)
    _ensure_column(conn, "title_parse", "chapter_title", "TEXT")
    _ensure_column(conn, "title_parse", "chapter_index_end", "REAL")
    _ensure_column(conn, "threads", "capture_mode", "TEXT NOT NULL DEFAULT 'full' CHECK(capture_mode IN ('text_only', 'full'))")
    _ensure_column(conn, "threads", "forum_id", "INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_threads_forum_tid "
        "ON threads(forum_id, tid) WHERE archive_status IN ('complete', 'partial')"
    )
    _ensure_column(conn, "threads", "content_kind", "TEXT")
    _ensure_column(conn, "threads", "primary_media_type", "TEXT")
    _ensure_column(conn, "threads", "category", "TEXT")
    _ensure_column(conn, "threads", "local_reply_count", "INTEGER")
    _ensure_column(conn, "threads", "reply_count_checked_at", "TEXT")
    _ensure_column(conn, "threads", "reply_count_mismatch_reason", "TEXT")
    _ensure_column(conn, "threads", "remote_last_reply_at_raw", "TEXT")
    _ensure_column(conn, "threads", "remote_last_reply_at", "TEXT")
    _ensure_column(conn, "threads", "remote_last_replier", "TEXT")
    _ensure_column(conn, "threads", "remote_reply_count", "INTEGER")
    _ensure_column(conn, "threads", "remote_observed_at", "TEXT")
    _ensure_column(conn, "threads", "remote_observed_from", "TEXT")
    _ensure_column(conn, "jobs", "paused_at", "TEXT")
    _ensure_column(conn, "jobs", "started_at", "TEXT")
    _ensure_column(conn, "jobs", "priority", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "jobs", "lease_token", "TEXT")
    _backfill_thread_forum_fields(conn)
    _ensure_column(conn, "forums", "name_en", "TEXT")
    _ensure_column(conn, "floors", "quote_text", "TEXT")
    _ensure_column(conn, "floors", "reply_text", "TEXT")
    _ensure_column(conn, "floors", "publisher_uid", "TEXT")
    _seed_default_forums(conn)
    # The superseded Job state was removed. Clean old rows on every SQLite
    # migration so databases upgraded from older releases converge safely.
    conn.execute(
        "DELETE FROM job_events WHERE job_id IN (SELECT job_id FROM jobs WHERE status = ?)",
        ("superseded",),
    )
    conn.execute("DELETE FROM jobs WHERE status = ?", ("superseded",))
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (1,),
    )
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_sql: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")


def _backfill_thread_forum_fields(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE threads SET forum_id = 30 WHERE forum_id IS NULL"
    )
    conn.execute(
        "UPDATE threads SET content_kind = 'comic' WHERE content_kind IS NULL"
    )
    conn.execute(
        "UPDATE threads SET primary_media_type = 'image' WHERE primary_media_type IS NULL"
    )


_DEFAULT_FORUMS = [
    (30, "漫画区", "comic", "https://bbs.yamibo.com", "Comic"),
    (55, "轻小说区", "novel", "https://bbs.yamibo.com", "Novel"),
    (5, "动漫区", "discussion", "https://bbs.yamibo.com", "Anime"),
    (33, "海域区", "discussion", "https://bbs.yamibo.com", "Watercooler"),
    (13, "贴图区", "discussion", "https://bbs.yamibo.com", "Image Board"),
    (16, "管理版", "discussion", "https://bbs.yamibo.com", "Admin"),
    (19, "资源交流区", "discussion", "https://bbs.yamibo.com", "Resources"),
    (44, "游戏区", "discussion", "https://bbs.yamibo.com", "Games"),
    (49, "文学区", "discussion", "https://bbs.yamibo.com", "Literature"),
    (370, "使用指南", "discussion", "https://bbs.yamibo.com", "Guide"),
    (379, "影视区", "discussion", "https://bbs.yamibo.com", "Film & TV"),
]


def _seed_default_forums(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT INTO forums (forum_id, name, content_kind, base_url, name_en)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(forum_id) DO UPDATE SET name = excluded.name, name_en = excluded.name_en
        """,
        _DEFAULT_FORUMS,
    )


def main() -> None:
    from yamibo_mcp.db.connection import connect

    parser = argparse.ArgumentParser(description="Initialize YamiboArchiver database.")
    parser.add_argument("--db", help="Override SQLite database path.")
    args = parser.parse_args()
    settings = load_settings()
    db_path = settings.db_path if args.db is None else settings.project_root / args.db
    conn = connect(db_path)
    try:
        migrate(conn, schema=settings.db_schema)
    finally:
        conn.close()
    print(f"Initialized database backend: {settings.db_backend}")


if __name__ == "__main__":
    main()
