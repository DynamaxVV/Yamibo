"""Remove the unused Agent Runtime tables after the 1.0 + Phase A+ cutover."""

from alembic import context, op
import sqlalchemy as sa

revision = "013_remove_legacy_agent_runtime"
down_revision = "012_add_agent_artifact_hashes"
branch_labels = None
depends_on = None

_LEGACY_TABLES = (
    "agent_citations",
    "agent_artifacts",
    "agent_approvals",
    "agent_run_events",
    "agent_steps",
    "agent_runs",
)


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names(schema=schema))
    for table in _LEGACY_TABLES:
        if table in existing:
            op.drop_table(table, schema=schema)


def downgrade() -> None:
    # The removed Runtime schema has no supported downgrade path.
    pass
