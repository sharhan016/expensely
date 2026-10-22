"""money expectations and their allocations

Revision ID: e3b5c7d9f106
Revises: d1a7b3c5e902
"""

import sqlalchemy as sa
from alembic import op

revision = "e3b5c7d9f106"
down_revision = "d1a7b3c5e902"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "money_expectations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "counterparty_id", sa.Integer(), sa.ForeignKey("counterparties.id", ondelete="SET NULL")
        ),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("people.id")),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id")),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_money_expectations_user_id", "money_expectations", ["user_id"])
    op.create_index(
        "ix_money_expectations_counterparty_id", "money_expectations", ["counterparty_id"]
    )
    op.create_table(
        "expectation_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "expectation_id",
            sa.Integer(),
            sa.ForeignKey("money_expectations.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "transaction_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="CASCADE")
        ),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("decision_source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_expectation_allocation_positive"),
    )
    op.create_index("ix_expectation_allocations_user_id", "expectation_allocations", ["user_id"])
    op.create_index(
        "ix_expectation_allocations_expectation_id", "expectation_allocations", ["expectation_id"]
    )
    op.create_index(
        "ix_expectation_allocations_transaction_id", "expectation_allocations", ["transaction_id"]
    )


def downgrade():
    op.drop_table("expectation_allocations")
    op.drop_table("money_expectations")
