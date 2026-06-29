from __future__ import annotations

from alembic import context, op

revision = "003_add_jobs_parent_created_index"
down_revision = "002_add_system_state"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    op.create_index("idx_jobs_parent_created", "jobs", ["parent_job_id", "created_at"], schema=_schema())


def downgrade() -> None:
    op.drop_index("idx_jobs_parent_created", table_name="jobs", schema=_schema())
