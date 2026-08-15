"""Keep the removed Runtime revision addressable for existing databases."""

revision = "009_add_agent_runtime"
down_revision = "008_add_rag_chunk_traceability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PONETAIL: this historical revision is intentionally a no-op; 013 removes
    # the old tables after Alembic can safely resolve databases at revision 012.
    pass


def downgrade() -> None:
    pass
