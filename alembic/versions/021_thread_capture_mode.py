"""Record whether a thread intentionally omits local image downloads."""

from alembic import context, op
import sqlalchemy as sa

revision = "021_thread_capture_mode"
down_revision = "020_indexed_thread_list_sorts"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("capture_mode", sa.Text(), nullable=False, server_default="full"),
        schema=_schema(),
    )
    op.create_check_constraint(
        "ck_threads_capture_mode", "threads",
        "capture_mode IN ('text_only', 'full')", schema=_schema(),
    )


def downgrade() -> None:
    op.drop_constraint("ck_threads_capture_mode", "threads", type_="check", schema=_schema())
    op.drop_column("threads", "capture_mode", schema=_schema())
