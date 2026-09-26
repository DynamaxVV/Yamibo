"""Persist manual daily brief issues, attempts, and immutable revisions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql


revision = "026_daily_briefs"
down_revision = "025_assistant_operation_phases"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "daily_issues",
        sa.Column("issue_id", sa.Text(), primary_key=True),
        sa.Column("source_kind", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("manual_issue_key", sa.Text(), nullable=True),
        sa.Column("rule_id", sa.Text(), nullable=True),
        sa.Column("rule_revision", sa.Integer(), nullable=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("target_day", sa.Date(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'created'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("source_kind IN ('manual', 'scheduled')", name="ck_daily_issues_source"),
        sa.CheckConstraint(
            "(source_kind = 'manual' AND manual_issue_key IS NOT NULL AND rule_id IS NULL AND rule_revision IS NULL) OR "
            "(source_kind = 'scheduled' AND manual_issue_key IS NULL AND rule_id IS NOT NULL AND rule_revision IS NOT NULL)",
            name="ck_daily_issues_source_key",
        ),
        sa.CheckConstraint("window_start < window_end", name="ck_daily_issues_window"),
        schema=schema,
    )
    op.create_index(
        "uq_daily_issues_manual_key",
        "daily_issues",
        ["manual_issue_key"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("source_kind = 'manual'"),
    )
    op.create_index(
        "uq_daily_issues_rule_day",
        "daily_issues",
        ["rule_id", "target_day"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("source_kind = 'scheduled'"),
    )
    op.create_index(
        "idx_daily_issues_target_day",
        "daily_issues",
        ["target_day", "created_at"],
        schema=schema,
    )
    op.create_table(
        "daily_attempts",
        sa.Column("attempt_id", sa.Text(), primary_key=True),
        sa.Column("issue_id", sa.Text(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_no > 0", name="ck_daily_attempts_positive_number"),
        sa.UniqueConstraint("issue_id", "attempt_no", name="uq_daily_attempts_issue_number"),
        sa.ForeignKeyConstraint(["issue_id"], [f"{schema}.daily_issues.issue_id"], ondelete="CASCADE"),
        schema=schema,
    )
    op.create_table(
        "daily_report_revisions",
        sa.Column("revision_id", sa.Text(), primary_key=True),
        sa.Column("issue_id", sa.Text(), nullable=False),
        sa.Column("report_revision", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("coverage_status", sa.Text(), nullable=False),
        sa.Column("report_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=True),
        sa.Column("stats_receipt_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_receipts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gap_reasons_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("regeneration_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("report_revision > 0", name="ck_daily_report_revisions_positive_number"),
        sa.CheckConstraint("coverage_status IN ('local_snapshot_only', 'partial', 'failed')", name="ck_daily_report_revisions_coverage"),
        sa.UniqueConstraint("issue_id", "report_revision", name="uq_daily_report_revisions_issue_number"),
        sa.ForeignKeyConstraint(["issue_id"], [f"{schema}.daily_issues.issue_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], [f"{schema}.daily_attempts.attempt_id"], ondelete="SET NULL"),
        schema=schema,
    )
    op.create_index(
        "idx_daily_report_revisions_issue_created",
        "daily_report_revisions",
        ["issue_id", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_daily_report_revisions_issue_created", table_name="daily_report_revisions", schema=schema)
    op.drop_table("daily_report_revisions", schema=schema)
    op.drop_table("daily_attempts", schema=schema)
    op.drop_index("idx_daily_issues_target_day", table_name="daily_issues", schema=schema)
    op.drop_index("uq_daily_issues_rule_day", table_name="daily_issues", schema=schema)
    op.drop_index("uq_daily_issues_manual_key", table_name="daily_issues", schema=schema)
    op.drop_table("daily_issues", schema=schema)
