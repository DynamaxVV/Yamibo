"""Associate daily brief issues and attempts with resumable daemon Jobs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op


revision = "027_daily_brief_jobs"
down_revision = "026_daily_briefs"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.add_column("daily_issues", sa.Column("queued_job_id", sa.Text(), nullable=True), schema=schema)
    op.create_foreign_key(
        "fk_daily_issues_queued_job_id_jobs",
        "daily_issues", "jobs", ["queued_job_id"], ["job_id"],
        source_schema=schema, referent_schema=schema, ondelete="SET NULL",
    )
    op.create_index(
        "uq_daily_issues_queued_job_id", "daily_issues", ["queued_job_id"],
        unique=True, schema=schema, postgresql_where=sa.text("queued_job_id IS NOT NULL"),
    )
    op.add_column("daily_attempts", sa.Column("job_id", sa.Text(), nullable=True), schema=schema)
    op.create_foreign_key(
        "fk_daily_attempts_job_id_jobs", "daily_attempts", "jobs", ["job_id"], ["job_id"],
        source_schema=schema, referent_schema=schema, ondelete="SET NULL",
    )
    op.create_index(
        "uq_daily_attempts_job_id", "daily_attempts", ["job_id"],
        unique=True, schema=schema, postgresql_where=sa.text("job_id IS NOT NULL"),
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("uq_daily_attempts_job_id", table_name="daily_attempts", schema=schema)
    op.drop_constraint("fk_daily_attempts_job_id_jobs", "daily_attempts", schema=schema, type_="foreignkey")
    op.drop_column("daily_attempts", "job_id", schema=schema)
    op.drop_index("uq_daily_issues_queued_job_id", table_name="daily_issues", schema=schema)
    op.drop_constraint("fk_daily_issues_queued_job_id_jobs", "daily_issues", schema=schema, type_="foreignkey")
    op.drop_column("daily_issues", "queued_job_id", schema=schema)
