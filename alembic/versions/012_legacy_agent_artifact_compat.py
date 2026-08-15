"""Keep the removed artifact-hash revision addressable for old databases."""

revision = "012_add_agent_artifact_hashes"
down_revision = "011_add_agent_runtime_orchestration_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
