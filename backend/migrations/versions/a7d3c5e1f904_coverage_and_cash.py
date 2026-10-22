"""coverage settings, reconciliation evidence and the cash bucket

Revision ID: a7d3c5e1f904
Revises: f6bc8d2e475
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d3c5e1f904"
down_revision = "f6bc8d2e475"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "accounts",
        sa.Column("in_scope", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("accounts", sa.Column("records_start", sa.Date(), nullable=True))
    op.add_column(
        "accounts",
        sa.Column("records_opening_balance", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.add_column("reconciliations", sa.Column("source_statement_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_reconciliations_source_statement",
        "reconciliations",
        "statement_imports",
        ["source_statement_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "reconciliations", sa.Column("movement_fingerprint", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "reconciliations",
        sa.Column("computed_difference", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.add_column(
        "reconciliations", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "reconciliations", sa.Column("invalidation_reason", sa.String(length=200), nullable=True)
    )
    op.create_table(
        "coverage_gap_unbridges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE")),
        sa.Column("gap_start", sa.Date(), nullable=False),
        sa.Column("gap_end", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "gap_start", "gap_end"),
    )
    op.create_index("ix_coverage_gap_unbridges_user_id", "coverage_gap_unbridges", ["user_id"])
    op.create_index(
        "ix_coverage_gap_unbridges_account_id", "coverage_gap_unbridges", ["account_id"]
    )
    op.create_table(
        "cash_withdrawal_markers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "transaction_id", sa.Integer(), sa.ForeignKey("transactions.id", ondelete="CASCADE")
        ),
        sa.Column("is_cash_withdrawal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="rule"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cash_withdrawal_markers_user_id", "cash_withdrawal_markers", ["user_id"])
    op.create_index(
        "ix_cash_withdrawal_markers_transaction_id",
        "cash_withdrawal_markers",
        ["transaction_id"],
        unique=True,
    )
    op.create_table(
        "cash_statement_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cash_statement_entries_user_id", "cash_statement_entries", ["user_id"])


def downgrade():
    op.drop_index("ix_cash_statement_entries_user_id", table_name="cash_statement_entries")
    op.drop_table("cash_statement_entries")
    op.drop_index("ix_cash_withdrawal_markers_transaction_id", table_name="cash_withdrawal_markers")
    op.drop_index("ix_cash_withdrawal_markers_user_id", table_name="cash_withdrawal_markers")
    op.drop_table("cash_withdrawal_markers")
    op.drop_index("ix_coverage_gap_unbridges_account_id", table_name="coverage_gap_unbridges")
    op.drop_index("ix_coverage_gap_unbridges_user_id", table_name="coverage_gap_unbridges")
    op.drop_table("coverage_gap_unbridges")
    op.drop_column("reconciliations", "invalidation_reason")
    op.drop_column("reconciliations", "invalidated_at")
    op.drop_column("reconciliations", "computed_difference")
    op.drop_column("reconciliations", "movement_fingerprint")
    op.drop_constraint("fk_reconciliations_source_statement", "reconciliations", type_="foreignkey")
    op.drop_column("reconciliations", "source_statement_id")
    op.drop_column("accounts", "records_opening_balance")
    op.drop_column("accounts", "records_start")
    op.drop_column("accounts", "in_scope")
