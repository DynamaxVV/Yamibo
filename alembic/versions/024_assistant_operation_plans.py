"""Persist frozen assistant archive/export plans and independent approvals."""

import sqlalchemy as sa
from alembic import context, op


revision = "024_assistant_operation_plans"
down_revision = "023_chat_discussion_evidence"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "assistant_operation_plans",
        sa.Column("plan_id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("plan_hash", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.Float(), nullable=True),
        sa.Column("approved_version", sa.Integer(), nullable=True),
        sa.Column("approved_hash", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint("action IN ('archive', 'export')", name="ck_assistant_operation_plan_action"),
        sa.UniqueConstraint("owner_id", "session_id", "request_key", name="uq_assistant_operation_plan_request"),
        schema=schema,
    )
    op.create_index(
        "idx_assistant_operation_plans_run",
        "assistant_operation_plans",
        ["owner_id", "session_id", "run_id", "created_at"],
        schema=schema,
    )
    op.create_table(
        "assistant_operation_items",
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("tid", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("item_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("plan_id", "tid", name="pk_assistant_operation_items"),
        sa.UniqueConstraint("plan_id", "ordinal", name="uq_assistant_operation_item_ordinal"),
        sa.ForeignKeyConstraint(
            ["plan_id"], [f"{schema}.assistant_operation_plans.plan_id"],
            name="fk_assistant_operation_item_plan", ondelete="CASCADE",
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("assistant_operation_items", schema=schema)
    op.drop_index("idx_assistant_operation_plans_run", table_name="assistant_operation_plans", schema=schema)
    op.drop_table("assistant_operation_plans", schema=schema)
