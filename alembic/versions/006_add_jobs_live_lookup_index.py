from __future__ import annotations

from alembic import context, op

revision = "006_add_jobs_live_lookup_index"
down_revision = "005_add_discussion_source_indexes"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.create_index(
        "idx_jobs_type_tid_status_created",
        "jobs",
        ["job_type", "tid", "status", "created_at", "job_id"],
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_jobs_type_tid_status_created", table_name="jobs", schema=schema)
