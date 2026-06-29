from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa

revision = "002_add_system_state"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def _table_kwargs() -> dict[str, str]:
    return {"schema": _schema()}


def upgrade() -> None:
    op.create_table(
        "system_state",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        **_table_kwargs(),
    )


def downgrade() -> None:
    op.drop_table("system_state", schema=_schema())
