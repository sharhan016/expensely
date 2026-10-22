"""remembered choices per person and a database cap on allocations

Revision ID: f4c6d8e0a217
Revises: e3b5c7d9f106
"""

import sqlalchemy as sa
from alembic import op

revision = "f4c6d8e0a217"
down_revision = "e3b5c7d9f106"
branch_labels = None
depends_on = None

FUNCTION = """
CREATE OR REPLACE FUNCTION enforce_allocation_cap() RETURNS trigger AS $$
DECLARE
    movement numeric;
    used numeric;
BEGIN
    IF NEW.status <> 'confirmed' THEN
        RETURN NEW;
    END IF;
    -- Lock the movement so two concurrent inserts cannot both fit.
    SELECT abs(amount) INTO movement FROM transactions WHERE id = NEW.transaction_id FOR UPDATE;
    SELECT COALESCE(SUM(amount), 0) INTO used FROM expectation_allocations
        WHERE transaction_id = NEW.transaction_id AND status = 'confirmed';
    IF used > movement THEN
        RAISE EXCEPTION 'allocations exceed the movement amount'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade():
    op.create_table(
        "motion_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column(
            "counterparty_id", sa.Integer(), sa.ForeignKey("counterparties.id", ondelete="CASCADE")
        ),
        sa.Column("choice", sa.String(length=16), nullable=False),
        sa.Column("after_transaction_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "counterparty_id"),
    )
    op.create_index("ix_motion_preferences_user_id", "motion_preferences", ["user_id"])
    op.create_index(
        "ix_motion_preferences_counterparty_id", "motion_preferences", ["counterparty_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(FUNCTION)
        op.execute(
            "CREATE CONSTRAINT TRIGGER expectation_allocation_cap "
            "AFTER INSERT OR UPDATE ON expectation_allocations "
            "DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION enforce_allocation_cap()"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS expectation_allocation_cap ON expectation_allocations")
        op.execute("DROP FUNCTION IF EXISTS enforce_allocation_cap()")
    op.drop_table("motion_preferences")
