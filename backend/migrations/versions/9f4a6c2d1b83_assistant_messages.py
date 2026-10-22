"""add private assistant messages

Revision ID: 9f4a6c2d1b83
Revises: 8b2f7d1e4a90
"""

import sqlalchemy as sa
from alembic import op

revision = "9f4a6c2d1b83"
down_revision = "8b2f7d1e4a90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_messages_user_id", "assistant_messages", ["user_id"])


def downgrade():
    op.drop_index("ix_assistant_messages_user_id", table_name="assistant_messages")
    op.drop_table("assistant_messages")
