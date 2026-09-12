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
"""
)
