"""Remove the obsolete superseded Job state and its historical rows."""

from alembic import context, op
import sqlalchemy as sa

revision = "014_remove_superseded_jobs"
down_revision = "013_remove_legacy_agent_runtime"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    jobs = sa.table("jobs", sa.column("job_id", sa.Text()), sa.column("status", sa.Text()), schema=schema)
    events = sa.table("job_events", sa.column("job_id", sa.Text()), schema=schema)
    job_ids = sa.select(jobs.c.job_id).where(jobs.c.status == "superseded")
    op.execute(events.delete().where(events.c.job_id.in_(job_ids)))
    op.execute(jobs.delete().where(jobs.c.status == "superseded"))


def downgrade() -> None:
    # Removed historical rows cannot be restored.
    pass
