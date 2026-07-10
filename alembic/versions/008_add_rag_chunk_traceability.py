from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa

revision = "008_add_rag_chunk_traceability"
down_revision = "007_add_thread_remote_observations"
branch_labels = None
depends_on = None


def _schema() -> str:
    return str(context.config.attributes.get("schema") or "public")


def upgrade() -> None:
    schema = _schema()
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("rag_chunks", schema=schema)}
    columns = (
        ("source_tid", sa.Integer()),
        ("source_pid", sa.Integer()),
        ("source_floor_no", sa.Integer()),
        ("cleaner_version", sa.Text()),
        ("chunker_version", sa.Text()),
        ("materializer_version", sa.Text()),
        ("source_hash", sa.Text()),
        ("generated_at", sa.Text()),
        ("quality_flags", sa.Text()),
    )
    for name, column_type in columns:
        if name not in existing:
            op.add_column("rag_chunks", sa.Column(name, column_type, nullable=True), schema=schema)


def downgrade() -> None:
    # These columns are also part of the current initial schema. Dropping them
    # here would corrupt databases created from that baseline.
    pass
