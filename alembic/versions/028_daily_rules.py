"""Persist owner-scoped daily brief schedules."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql


revision = "028_daily_rules"
down_revision = "027_daily_brief_jobs"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "daily_rules",
        sa.Column("rule_id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("forum_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("execution_time", sa.Time(), nullable=False),
        sa.Column("preparation_deadline", sa.Time(), nullable=False),
        sa.Column("budget_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("revision > 0", name="ck_daily_rules_revision_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(forum_ids) = 'array' AND jsonb_array_length(forum_ids) > 0",
            name="ck_daily_rules_forum_ids_array",
        ),
        schema=schema,
    )
    op.create_index("idx_daily_rules_due", "daily_rules", ["next_run_at", "rule_id"],
                    schema=schema, postgresql_where=sa.text("enabled"))
    op.create_index("idx_daily_rules_owner", "daily_rules", ["owner_id", "updated_at"], schema=schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_daily_rules_owner", table_name="daily_rules", schema=schema)
    op.drop_index("idx_daily_rules_due", table_name="daily_rules", schema=schema)
    op.drop_table("daily_rules", schema=schema)
