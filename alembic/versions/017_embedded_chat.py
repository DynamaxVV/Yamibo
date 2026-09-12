"""Persist local chat, operation receipts and file ownership."""

import re

from alembic import context, op

revision = "017_embedded_chat"
down_revision = "016_add_jobs_started_at"
branch_labels = None
depends_on = None
SQL = "CREATE TABLE IF NOT EXISTS chat_files (id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS idx_chat_files_parent ON chat_files(parent_id);\nCREATE TABLE IF NOT EXISTS chat_operations (id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS idx_chat_operations_parent ON chat_operations(parent_id);\nCREATE TABLE IF NOT EXISTS chat_runs (id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS idx_chat_runs_parent ON chat_runs(parent_id);\nCREATE TABLE IF NOT EXISTS chat_sessions (id TEXT PRIMARY KEY, parent_id TEXT NOT NULL, data TEXT NOT NULL);CREATE INDEX IF NOT EXISTS idx_chat_sessions_parent ON chat_sessions(parent_id);\nCREATE TABLE IF NOT EXISTS chat_requests (\n session_id TEXT NOT NULL, request_id TEXT NOT NULL, run_id TEXT NOT NULL,\n PRIMARY KEY(session_id, request_id)\n);\nCREATE TABLE IF NOT EXISTS chat_events (\n run_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL,\n PRIMARY KEY(run_id, seq)\n);\n"


def upgrade():
    schema = str(context.config.attributes.get("schema") or "public").replace('"', '""')
    sql = re.sub(
        r"\bchat_(sessions|runs|operations|files|requests|events)\b",
        lambda m: f'"{schema}".{m.group(0)}',
        SQL,
    )
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for name in ("events", "requests", "files", "operations", "runs", "sessions"):
        schema = str(context.config.attributes.get("schema") or "public").replace(
            chr(34), chr(34) * 2
        )
        op.execute(f'DROP TABLE "{schema}".chat_{name}')
