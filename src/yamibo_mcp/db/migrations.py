from __future__ import annotations

import argparse
import sqlite3

from yamibo_mcp.config import load_settings
from yamibo_mcp.db.connection import connect


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  parent_job_id TEXT,
  job_type TEXT NOT NULL,
  tid INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  stage TEXT,
  progress_current INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER,
  worker_id TEXT,
  heartbeat_at TEXT,
  lease_until TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 3,
  resumable INTEGER NOT NULL DEFAULT 1,
  cancel_requested_at TEXT,
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
  permission INTEGER NOT NULL DEFAULT 0 CHECK(permission >= 0),
  is_finished INTEGER NOT NULL DEFAULT 0,
  image_count INTEGER NOT NULL DEFAULT 0 CHECK(image_count >= 0),
  context_path TEXT,
  archive_status TEXT NOT NULL DEFAULT 'stale',
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

CREATE TABLE IF NOT EXISTS floors (
  pid INTEGER PRIMARY KEY,
  tid INTEGER NOT NULL REFERENCES threads(tid),
  floor_no INTEGER NOT NULL CHECK(floor_no > 0),
  publisher TEXT,
  content TEXT,
  pub_time TEXT,
  has_images INTEGER NOT NULL DEFAULT 0,
  content_hash TEXT
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
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executescript(FTS_SQL)
    _ensure_column(conn, "title_parse", "chapter_title", "TEXT")
    _ensure_column(conn, "title_parse", "chapter_index_end", "REAL")
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
        (1,),
    )
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_sql: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize YamiboMCP database.")
    parser.add_argument("--db", help="Override SQLite database path.")
    args = parser.parse_args()
    settings = load_settings()
    db_path = settings.db_path if args.db is None else settings.project_root / args.db
    conn = connect(db_path)
    try:
        migrate(conn)
    finally:
        conn.close()
    print(f"Initialized database: {db_path}")


if __name__ == "__main__":
    main()
