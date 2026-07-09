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
    op.add_column("rag_chunks", sa.Column("source_tid", sa.Integer(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("source_pid", sa.Integer(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("source_floor_no", sa.Integer(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("cleaner_version", sa.Text(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("chunker_version", sa.Text(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("materializer_version", sa.Text(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("source_hash", sa.Text(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("generated_at", sa.Text(), nullable=True), schema=schema)
    op.add_column("rag_chunks", sa.Column("quality_flags", sa.Text(), nullable=True), schema=schema)


def downgrade() -> None:
    schema = _schema()
    op.drop_column("rag_chunks", "quality_flags", schema=schema)
    op.drop_column("rag_chunks", "generated_at", schema=schema)
    op.drop_column("rag_chunks", "source_hash", schema=schema)
    op.drop_column("rag_chunks", "materializer_version", schema=schema)
    op.drop_column("rag_chunks", "chunker_version", schema=schema)
    op.drop_column("rag_chunks", "cleaner_version", schema=schema)
    op.drop_column("rag_chunks", "source_floor_no", schema=schema)
    op.drop_column("rag_chunks", "source_pid", schema=schema)
    op.drop_column("rag_chunks", "source_tid", schema=schema)
