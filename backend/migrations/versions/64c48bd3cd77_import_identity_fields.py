"""add import identity fields

Revision ID: 64c48bd3cd77
Revises: 27ad2475775d
"""

import sqlalchemy as sa
from alembic import op

revision = "64c48bd3cd77"
down_revision = "27ad2475775d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "statement_imports",
        sa.Column("account_hint", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "statement_imports",
        sa.Column("statement_kind", sa.String(length=24), nullable=False, server_default="bank"),
    )


def downgrade():
    op.drop_column("statement_imports", "statement_kind")
    op.drop_column("statement_imports", "account_hint")
