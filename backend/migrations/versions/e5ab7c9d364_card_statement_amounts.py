"""separate credit-card statement amounts

Revision ID: e5ab7c9d364
Revises: d4fa6b8c253
"""

import sqlalchemy as sa
from alembic import op

revision = "e5ab7c9d364"
down_revision = "d4fa6b8c253"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "statement_imports",
        sa.Column("statement_balance", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.add_column(
        "statement_imports",
        sa.Column("total_amount_due", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.add_column(
        "statement_imports",
        sa.Column("minimum_amount_due", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.add_column(
        "statement_imports",
        sa.Column("available_credit", sa.Numeric(precision=16, scale=2), nullable=True),
    )
    op.execute(
        """
        UPDATE statement_imports
           SET total_amount_due = ABS(closing_balance),
               closing_balance = NULL,
               reconciliation_status = 'unavailable',
               reconciliation_difference = NULL
         WHERE statement_kind = 'credit_card'
           AND closing_balance IS NOT NULL
        """
    )


def downgrade():
    op.execute(
        """
        UPDATE statement_imports
           SET closing_balance = -total_amount_due
         WHERE statement_kind = 'credit_card'
           AND closing_balance IS NULL
           AND total_amount_due IS NOT NULL
        """
    )
    op.drop_column("statement_imports", "available_credit")
    op.drop_column("statement_imports", "minimum_amount_due")
    op.drop_column("statement_imports", "total_amount_due")
    op.drop_column("statement_imports", "statement_balance")
