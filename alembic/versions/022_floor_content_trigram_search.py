"""Add indexed substring search for archived floor text."""

from alembic import context, op


revision = "022_floor_content_trigram_search"
down_revision = "021_thread_capture_mode"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    # pg_trgm is installed by the initial PostgreSQL schema migration. This normal
    # transactional CREATE INDEX is reversible, but PostgreSQL holds a lock that
    # blocks writes to floors for the duration of the build. Do not apply on a busy
    # populated database without a maintenance window. The current migration runner
    # wraps migrations in a transaction and does not support Alembic autocommit_block.
    op.create_index(
        "idx_floors_content_trgm",
        "floors",
        ["content"],
        schema=_schema(),
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("idx_floors_content_trgm", table_name="floors", schema=_schema())
