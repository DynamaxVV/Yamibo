"""Record when a worker starts the current job attempt."""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision = "016_add_jobs_started_at"
down_revision = "015_add_jobs_priority"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        schema=_schema(),
    )


def downgrade() -> None:
    op.drop_column("jobs", "started_at", schema=_schema())
