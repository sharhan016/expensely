"""add statement overlap warning

Revision ID: c41ce7df902a
Revises: b13cb041e8e2
"""

import sqlalchemy as sa
from alembic import op

revision = "c41ce7df902a"
down_revision = "b13cb041e8e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "statement_imports",
        sa.Column("overlap_warning", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("statement_imports", "overlap_warning")
