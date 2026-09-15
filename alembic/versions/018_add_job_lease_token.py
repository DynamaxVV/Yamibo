"""Fence stale daemon workers after a job lease is reclaimed."""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision = "018_add_job_lease_token"
down_revision = "017_embedded_chat"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("lease_token", sa.Text(), nullable=True),
        schema=_schema(),
    )


def downgrade() -> None:
    op.drop_column("jobs", "lease_token", schema=_schema())
