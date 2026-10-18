"""store detected account display name

Revision ID: c42da918f821
Revises: c41ce7df902a
"""

import sqlalchemy as sa
from alembic import op

revision = "c42da918f821"
down_revision = "c41ce7df902a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "statement_imports",
        sa.Column(
            "detected_account_name", sa.String(length=120), nullable=False, server_default=""
        ),
    )
    op.execute(
        "UPDATE statement_imports SET detected_account_name = "
        "'YES BANK Uni RuPay Credit Card' "
        "WHERE bank_slug = 'yes' AND statement_kind = 'credit_card'"
    )
    op.execute(
        "UPDATE accounts SET name = 'YES BANK Uni RuPay Credit Card' "
        "WHERE bank_slug = 'yes' AND kind = 'credit_card' "
        "AND lower(name) IN ('yes account', 'yes bank account')"
    )


def downgrade():
    op.drop_column("statement_imports", "detected_account_name")
