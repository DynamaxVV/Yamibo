"""Keep the removed step-lease revision addressable for old databases."""

revision = "010_add_agent_step_execution_lease"
down_revision = "009_add_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
