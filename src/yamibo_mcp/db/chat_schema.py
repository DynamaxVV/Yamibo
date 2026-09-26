"""SQLite bootstrap schema for the embedded chat repositories."""

TABLES = {"sessions", "runs", "operations", "files"}
SCHEMA = (
    "\n".join(
        f"CREATE TABLE IF NOT EXISTS chat_{name} (id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, data TEXT NOT NULL);"
        f"CREATE INDEX IF NOT EXISTS idx_chat_{name}_parent ON chat_{name}(parent_id);"
        for name in sorted(TABLES)
    )
    + """
CREATE TABLE IF NOT EXISTS chat_requests (
 session_id TEXT NOT NULL, request_id TEXT NOT NULL, run_id TEXT NOT NULL,
 PRIMARY KEY(session_id, request_id)
);
CREATE TABLE IF NOT EXISTS chat_events (
 run_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(run_id, seq)
);
CREATE TABLE IF NOT EXISTS chat_discussion_scopes (
 scope_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL,
 run_id TEXT NOT NULL UNIQUE, mode TEXT NOT NULL, forum_ids TEXT NOT NULL,
 tids TEXT NOT NULL, pids TEXT NOT NULL, start_at TEXT, end_at TEXT,
 created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_discussion_scopes_owner_run
 ON chat_discussion_scopes(owner_id, session_id, run_id);
CREATE TABLE IF NOT EXISTS chat_source_receipts (
 receipt_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, owner_id TEXT NOT NULL,
 session_id TEXT NOT NULL, run_id TEXT NOT NULL, tid INTEGER NOT NULL,
 pid INTEGER NOT NULL, floor_no INTEGER NOT NULL, content_hash TEXT NOT NULL,
 paragraph_start INTEGER NOT NULL, paragraph_end INTEGER NOT NULL, partial_paragraph INTEGER,
 content TEXT NOT NULL, truncated INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_source_receipts_run ON chat_source_receipts(run_id, receipt_id);
CREATE TABLE IF NOT EXISTS assistant_operation_plans (
 plan_id TEXT PRIMARY KEY,
 owner_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 run_id TEXT,
 request_key TEXT NOT NULL,
 request_fingerprint TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('archive', 'export')),
 status TEXT NOT NULL,
 plan_version INTEGER NOT NULL,
 plan_hash TEXT NOT NULL,
 plan_json TEXT NOT NULL,
 approved_at REAL,
 approved_version INTEGER,
 approved_hash TEXT,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 UNIQUE(owner_id, session_id, request_key)
);
CREATE INDEX IF NOT EXISTS idx_assistant_operation_plans_run
 ON assistant_operation_plans(owner_id, session_id, run_id, created_at);
CREATE TABLE IF NOT EXISTS assistant_operation_items (
 plan_id TEXT NOT NULL,
 tid INTEGER NOT NULL,
 ordinal INTEGER NOT NULL,
 stage TEXT NOT NULL,
 item_json TEXT NOT NULL,
 created_at REAL NOT NULL,
 updated_at REAL NOT NULL,
 PRIMARY KEY(plan_id, tid),
 UNIQUE(plan_id, ordinal)
);
CREATE TABLE IF NOT EXISTS assistant_operation_phases (
 phase_key TEXT PRIMARY KEY,
 plan_id TEXT NOT NULL,
 tid INTEGER NOT NULL,
 step_index INTEGER NOT NULL,
 state TEXT NOT NULL,
 job_id TEXT,
 error_code TEXT,
 error_message TEXT,
 export_path TEXT,
 updated_at REAL NOT NULL,
 UNIQUE(plan_id, tid, step_index),
 FOREIGN KEY(plan_id) REFERENCES assistant_operation_plans(plan_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_assistant_operation_phases_job
 ON assistant_operation_phases(job_id);
"""
)
