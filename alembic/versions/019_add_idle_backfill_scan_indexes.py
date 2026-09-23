"""Speed up bounded forum scans ordered by thread id."""

from alembic import context, op
import sqlalchemy as sa


revision = "019_add_idle_backfill_scan_indexes"
down_revision = "018_add_job_lease_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = str(context.config.attributes.get("schema") or "public")
    op.create_index(
        "idx_threads_forum_tid",
        "threads",
        ["forum_id", "tid"],
        schema=schema,
        if_not_exists=True,
        postgresql_where=sa.text("archive_status IN ('complete', 'partial')"),
    )
    op.create_index(
        "idx_assets_tid_pid_type",
        "assets",
        ["tid", "pid", "asset_type"],
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = str(context.config.attributes.get("schema") or "public")
    op.drop_index("idx_assets_tid_pid_type", table_name="assets", schema=schema)
    op.drop_index("idx_threads_forum_tid", table_name="threads", schema=schema)
