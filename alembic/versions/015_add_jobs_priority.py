"""Add explicit queue priority for foreground-first maintenance scheduling."""

from alembic import context, op
import sqlalchemy as sa

revision = "015_add_jobs_priority"
down_revision = "014_remove_superseded_jobs"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    op.add_column("jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")), schema=_schema())


def downgrade() -> None:
    op.drop_column("jobs", "priority", schema=_schema())
