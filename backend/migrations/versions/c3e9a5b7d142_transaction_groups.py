"""add analytical transaction groups

Revision ID: c3e9a5b7d142
Revises: b2d8f4a6c031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3e9a5b7d142"
down_revision = "b2d8f4a6c031"
branch_labels = None
depends_on = None

purpose_enum = postgresql.ENUM(
    "PERSONAL",
    "BUSINESS",
    "INCOME",
    "TRANSFER",
    "LOAN_GIVEN",
    "LOAN_REPAYMENT",
    "ENTRUSTED",
    "REIMBURSEMENT",
    "REFUND",
    "OTHER",
    name="purpose",
    create_type=False,
)


def upgrade():
    op.create_table(
        "transaction_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("counterparty_id", sa.Integer(), nullable=False),
        sa.Column("purpose", purpose_enum, nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("group_key", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_key"),
        sa.UniqueConstraint(
            "user_id",
            "counterparty_id",
            "purpose",
            "direction",
            name="uq_transaction_group_owner_meaning_direction",
        ),
    )
    op.create_index("ix_transaction_groups_user_id", "transaction_groups", ["user_id"])
    op.create_index(
        "ix_transaction_groups_counterparty_id", "transaction_groups", ["counterparty_id"]
    )
    op.create_table(
        "transaction_group_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["transaction_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id"),
    )
    op.create_index(
        "ix_transaction_group_members_user_id", "transaction_group_members", ["user_id"]
    )
    op.create_index(
        "ix_transaction_group_members_group_id", "transaction_group_members", ["group_id"]
    )
    op.create_index(
        "ix_transaction_group_members_transaction_id",
        "transaction_group_members",
        ["transaction_id"],
        unique=True,
    )
    op.execute(
        """
        INSERT INTO transaction_groups
            (user_id, counterparty_id, purpose, direction, group_key, created_at, updated_at)
        SELECT DISTINCT
            m.user_id,
            m.counterparty_id,
            m.purpose,
            CASE WHEN t.amount < 0 THEN 'paid' ELSE 'received' END,
            CONCAT(
                m.user_id, ':', m.counterparty_id, ':', LOWER(m.purpose::text), ':',
                CASE WHEN t.amount < 0 THEN 'paid' ELSE 'received' END
            ),
            NOW(),
            NOW()
        FROM transaction_meanings m
        JOIN transactions t ON t.id = m.transaction_id
        WHERE m.counterparty_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO transaction_group_members (user_id, group_id, transaction_id)
        SELECT m.user_id, g.id, m.transaction_id
        FROM transaction_meanings m
        JOIN transactions t ON t.id = m.transaction_id
        JOIN transaction_groups g
          ON g.user_id = m.user_id
         AND g.counterparty_id = m.counterparty_id
         AND g.purpose = m.purpose
         AND g.direction = CASE WHEN t.amount < 0 THEN 'paid' ELSE 'received' END
        WHERE m.counterparty_id IS NOT NULL
        """
    )


def downgrade():
    op.drop_index(
        "ix_transaction_group_members_transaction_id", table_name="transaction_group_members"
    )
    op.drop_index("ix_transaction_group_members_group_id", table_name="transaction_group_members")
    op.drop_index("ix_transaction_group_members_user_id", table_name="transaction_group_members")
    op.drop_table("transaction_group_members")
    op.drop_index("ix_transaction_groups_counterparty_id", table_name="transaction_groups")
    op.drop_index("ix_transaction_groups_user_id", table_name="transaction_groups")
    op.drop_table("transaction_groups")
