"""add statement reconciliation evidence

Revision ID: a1c7e3f5b920
Revises: 9f4a6c2d1b83
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c7e3f5b920"
down_revision = "9f4a6c2d1b83"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "statement_imports",
        sa.Column(
            "reconciliation_status",
            sa.String(length=20),
            nullable=False,
            server_default="unavailable",
        ),
    )
    op.add_column(
        "statement_imports",
        sa.Column("reconciliation_difference", sa.Numeric(precision=16, scale=2), nullable=True),
    )


def downgrade():
    op.drop_column("statement_imports", "reconciliation_difference")
    op.drop_column("statement_imports", "reconciliation_status")
