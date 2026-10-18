"""add trip purchase windows and exclusions

Revision ID: d53eb029a932
Revises: c42da918f821
"""

import sqlalchemy as sa
from alembic import op

revision = "d53eb029a932"
down_revision = "c42da918f821"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trips", sa.Column("purchase_window_start", sa.Date(), nullable=True))
    op.add_column("trips", sa.Column("purchase_window_end", sa.Date(), nullable=True))
    op.execute("UPDATE trips SET purchase_window_start = starts_on, purchase_window_end = ends_on")
    op.create_table(
        "trip_transaction_exclusions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trip_id", "transaction_id"),
    )
    op.create_index(
        "ix_trip_transaction_exclusions_trip_id",
        "trip_transaction_exclusions",
        ["trip_id"],
    )
    op.create_index(
        "ix_trip_transaction_exclusions_transaction_id",
        "trip_transaction_exclusions",
        ["transaction_id"],
    )


def downgrade():
    op.drop_index(
        "ix_trip_transaction_exclusions_transaction_id",
        table_name="trip_transaction_exclusions",
    )
    op.drop_index(
        "ix_trip_transaction_exclusions_trip_id",
        table_name="trip_transaction_exclusions",
    )
    op.drop_table("trip_transaction_exclusions")
    op.drop_column("trips", "purchase_window_end")
    op.drop_column("trips", "purchase_window_start")
