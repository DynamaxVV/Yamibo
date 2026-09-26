"""Durable generic scheduled tasks and occurrence ledger."""
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "029_scheduled_tasks"
down_revision = "028_daily_rules"
branch_labels = None
depends_on = None


def upgrade():
    schema = str(context.config.attributes.get("schema") or "public")
    op.create_table("scheduled_tasks",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("arguments", JSONB(), nullable=False),
        sa.Column("schedule_kind", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("cron", sa.Text()),
        sa.Column("at", sa.DateTime(timezone=True)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint("(schedule_kind='at' AND at IS NOT NULL AND cron IS NULL) OR (schedule_kind='cron' AND cron IS NOT NULL AND at IS NULL)"),
        schema=schema)
    op.create_index("idx_scheduled_tasks_due", "scheduled_tasks", ["next_run_at"], schema=schema, postgresql_where=sa.text("enabled"))
    op.create_table("scheduled_task_occurrences",
        sa.Column("task_id", sa.Text(), sa.ForeignKey(f"{schema}.scheduled_tasks.task_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("manual", sa.Boolean(), primary_key=True, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), schema=schema)


def downgrade():
    schema = str(context.config.attributes.get("schema") or "public")
    op.drop_table("scheduled_task_occurrences", schema=schema)
    op.drop_table("scheduled_tasks", schema=schema)
