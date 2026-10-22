"""add transaction links

Revision ID: 8b2f7d1e4a90
Revises: 6e4b1a9c2d77
"""

import sqlalchemy as sa
from alembic import op

revision = "8b2f7d1e4a90"
down_revision = "6e4b1a9c2d77"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transaction_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("link_type", sa.String(length=32), nullable=False),
        sa.Column("outflow_transaction_id", sa.Integer(), nullable=False),
        sa.Column("inflow_transaction_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="confirmed"),
        sa.Column("confidence", sa.String(length=16), nullable=False, server_default="high"),
        sa.Column(
            "decision_source",
            sa.String(length=32),
            nullable=False,
            server_default="automatic_rule",
        ),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inflow_transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["outflow_transaction_id"], ["transactions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "outflow_transaction_id",
            "inflow_transaction_id",
            name="uq_transaction_link_owner_pair",
        ),
    )
    op.create_index(
        "ix_transaction_links_inflow_transaction_id",
        "transaction_links",
        ["inflow_transaction_id"],
    )
    op.create_index(
        "ix_transaction_links_outflow_transaction_id",
        "transaction_links",
        ["outflow_transaction_id"],
    )
    op.create_index("ix_transaction_links_user_id", "transaction_links", ["user_id"])


def downgrade():
    op.drop_index("ix_transaction_links_user_id", table_name="transaction_links")
    op.drop_index("ix_transaction_links_outflow_transaction_id", table_name="transaction_links")
    op.drop_index("ix_transaction_links_inflow_transaction_id", table_name="transaction_links")
    op.drop_table("transaction_links")
