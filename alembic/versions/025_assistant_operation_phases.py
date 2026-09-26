"""Track unique, resumable phases in approved assistant operation plans."""

import sqlalchemy as sa
from alembic import context, op


revision = "025_assistant_operation_phases"
down_revision = "024_assistant_operation_plans"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "assistant_operation_phases",
        sa.Column("phase_key", sa.Text(), primary_key=True),
        sa.Column("plan_id", sa.Text(), nullable=False),
        sa.Column("tid", sa.BigInteger(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("export_path", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("plan_id", "tid", "step_index", name="uq_assistant_operation_phase_step"),
        sa.ForeignKeyConstraint(
            ["plan_id"], [f"{schema}.assistant_operation_plans.plan_id"],
            name="fk_assistant_operation_phase_plan", ondelete="CASCADE",
        ),
        schema=schema,
    )
    op.create_index(
        "idx_assistant_operation_phases_job",
        "assistant_operation_phases", ["job_id"], schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_assistant_operation_phases_job", table_name="assistant_operation_phases", schema=schema)
    op.drop_table("assistant_operation_phases", schema=schema)
