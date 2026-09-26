"""Persist frozen discussion scopes and source-read receipts."""

import sqlalchemy as sa
from alembic import context, op


revision = "023_chat_discussion_evidence"
down_revision = "022_floor_content_trigram_search"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "chat_discussion_scopes",
        sa.Column("scope_id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False, unique=True),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("forum_ids", sa.Text(), nullable=False),
        sa.Column("tids", sa.Text(), nullable=False),
        sa.Column("pids", sa.Text(), nullable=False),
        sa.Column("start_at", sa.Text(), nullable=True),
        sa.Column("end_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_chat_discussion_scopes_owner_run",
        "chat_discussion_scopes",
        ["owner_id", "session_id", "run_id"],
        schema=schema,
    )
    op.create_table(
        "chat_source_receipts",
        sa.Column("receipt_id", sa.Text(), primary_key=True),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("tid", sa.BigInteger(), nullable=False),
        sa.Column("pid", sa.BigInteger(), nullable=False),
        sa.Column("floor_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("paragraph_start", sa.Integer(), nullable=False),
        sa.Column("paragraph_end", sa.Integer(), nullable=False),
        sa.Column("partial_paragraph", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        schema=schema,
    )
    op.create_index(
        "idx_chat_source_receipts_run",
        "chat_source_receipts",
        ["run_id", "receipt_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_chat_source_receipts_run", table_name="chat_source_receipts", schema=schema)
    op.drop_table("chat_source_receipts", schema=schema)
    op.drop_index("idx_chat_discussion_scopes_owner_run", table_name="chat_discussion_scopes", schema=schema)
    op.drop_table("chat_discussion_scopes", schema=schema)
