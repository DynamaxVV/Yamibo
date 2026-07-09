from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa

revision = "007_add_thread_remote_observations"
down_revision = "006_add_jobs_live_lookup_index"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    op.add_column("threads", sa.Column("local_reply_count", sa.Integer(), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("reply_count_checked_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("reply_count_mismatch_reason", sa.Text(), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("remote_last_reply_at_raw", sa.Text(), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("remote_last_reply_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("remote_last_replier", sa.Text(), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("remote_reply_count", sa.Integer(), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("remote_observed_at", sa.DateTime(timezone=True), nullable=True), schema=schema)
    op.add_column("threads", sa.Column("remote_observed_from", sa.Text(), nullable=True), schema=schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_column("threads", "remote_observed_from", schema=schema)
    op.drop_column("threads", "remote_observed_at", schema=schema)
    op.drop_column("threads", "remote_reply_count", schema=schema)
    op.drop_column("threads", "remote_last_replier", schema=schema)
    op.drop_column("threads", "remote_last_reply_at", schema=schema)
    op.drop_column("threads", "remote_last_reply_at_raw", schema=schema)
    op.drop_column("threads", "reply_count_mismatch_reason", schema=schema)
    op.drop_column("threads", "reply_count_checked_at", schema=schema)
    op.drop_column("threads", "local_reply_count", schema=schema)
