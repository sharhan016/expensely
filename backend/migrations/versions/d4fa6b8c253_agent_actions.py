"""add reversible agent actions

Revision ID: d4fa6b8c253
Revises: c3e9a5b7d142
"""

import sqlalchemy as sa
from alembic import op

revision = "d4fa6b8c253"
down_revision = "c3e9a5b7d142"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=48), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("proposal", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "decision_source",
            sa.String(length=32),
            nullable=False,
            server_default="assistant_proposal",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_actions_user_id", "agent_actions", ["user_id"])
    op.create_index("ix_agent_actions_status", "agent_actions", ["status"])


def downgrade():
    op.drop_index("ix_agent_actions_status", table_name="agent_actions")
    op.drop_index("ix_agent_actions_user_id", table_name="agent_actions")
    op.drop_table("agent_actions")
