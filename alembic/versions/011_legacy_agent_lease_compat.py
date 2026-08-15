"""Keep the removed orchestration-lease revision addressable for old databases."""

revision = "011_add_agent_runtime_orchestration_lease"
down_revision = "010_add_agent_step_execution_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
