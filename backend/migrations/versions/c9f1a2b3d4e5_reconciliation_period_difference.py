"""store the whole-period difference beside each statement's own result

Revision ID: c9f1a2b3d4e5
Revises: a7d3c5e1f904
"""

import sqlalchemy as sa
from alembic import op

revision = "c9f1a2b3d4e5"
down_revision = "a7d3c5e1f904"
branch_labels = None
depends_on = None


def upgrade():
    # NULL on existing rows: they read as unverified until "Re-check records".
    op.add_column(
        "reconciliations",
        sa.Column("period_difference", sa.Numeric(precision=16, scale=2), nullable=True),
    )


def downgrade():
    op.drop_column("reconciliations", "period_difference")
