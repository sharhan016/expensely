"""separate immutable movements from audited meanings

Revision ID: f2d8a6b1947c
Revises: e7c31f6a9420
"""

import sqlalchemy as sa
from alembic import op

revision = "f2d8a6b1947c"
down_revision = "e7c31f6a9420"
branch_labels = None
depends_on = None


def upgrade():
    purpose = sa.Enum(
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
    op.create_table(
        "transaction_meanings",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", purpose, nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("employer_id", sa.Integer(), nullable=True),
        sa.Column("trip_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_status", sa.String(length=20), nullable=False, server_default="current"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    op.create_index("ix_transaction_meanings_user_id", "transaction_meanings", ["user_id"])
    op.create_table(
        "transaction_meaning_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("before_values", sa.JSON(), nullable=False),
        sa.Column("after_values", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transaction_meaning_changes_transaction_id",
        "transaction_meaning_changes",
        ["transaction_id"],
    )
    op.create_index(
        "ix_transaction_meaning_changes_user_id", "transaction_meaning_changes", ["user_id"]
    )
    op.add_column(
        "reconciliations", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "reconciliations",
        sa.Column(
            "classification_status",
            sa.String(length=20),
            nullable=False,
            server_default="current",
        ),
    )
    op.execute(
        """
        INSERT INTO transaction_meanings (
            transaction_id, user_id, purpose, person_id, employer_id, trip_id,
            notes, version, review_status, created_at, updated_at
        )
        SELECT
            id, user_id, purpose, person_id, employer_id, trip_id,
            notes, 1, 'current', created_at, updated_at
        FROM transactions
        ON CONFLICT (transaction_id) DO NOTHING
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF (SELECT count(*) FROM transactions) <>
               (SELECT count(*) FROM transaction_meanings) THEN
                RAISE EXCEPTION 'Transaction meaning backfill count mismatch';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM transaction_meanings m
                JOIN transactions t ON t.id = m.transaction_id
                WHERE m.user_id <> t.user_id
                   OR m.purpose <> t.purpose
                   OR m.person_id IS DISTINCT FROM t.person_id
                   OR m.employer_id IS DISTINCT FROM t.employer_id
                   OR m.trip_id IS DISTINCT FROM t.trip_id
                   OR m.notes IS DISTINCT FROM t.notes
            ) THEN
                RAISE EXCEPTION 'Transaction meaning backfill value mismatch';
            END IF;
        END
        $$
        """
    )


def downgrade():
    op.drop_column("reconciliations", "classification_status")
    op.drop_column("reconciliations", "finalized_at")
    op.drop_index(
        "ix_transaction_meaning_changes_user_id", table_name="transaction_meaning_changes"
    )
    op.drop_index(
        "ix_transaction_meaning_changes_transaction_id",
        table_name="transaction_meaning_changes",
    )
    op.drop_table("transaction_meaning_changes")
    op.drop_index("ix_transaction_meanings_user_id", table_name="transaction_meanings")
    op.drop_table("transaction_meanings")
