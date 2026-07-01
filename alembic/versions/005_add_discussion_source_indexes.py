from __future__ import annotations

from alembic import context, op

revision = "005_add_discussion_source_indexes"
down_revision = "004_add_discussion_trend_mart"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.create_index(
        "idx_threads_forum_pub_time",
        "threads",
        ["forum_id", "pub_time"],
        schema=schema,
        if_not_exists=True,
    )
    op.create_index(
        "idx_floors_tid_pub_time",
        "floors",
        ["tid", "pub_time"],
        schema=schema,
        if_not_exists=True,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("idx_floors_tid_pub_time", table_name="floors", schema=schema)
    op.drop_index("idx_threads_forum_pub_time", table_name="threads", schema=schema)
