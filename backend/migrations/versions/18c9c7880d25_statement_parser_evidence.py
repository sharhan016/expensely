"""store statement parser evidence

Revision ID: 18c9c7880d25
Revises: f2d8a6b1947c
"""

import sqlalchemy as sa
from alembic import op

revision = "18c9c7880d25"
down_revision = "f2d8a6b1947c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "statement_imports",
        sa.Column(
            "instrument_confidence",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "statement_imports",
        sa.Column(
            "parser_name",
            sa.String(length=80),
            nullable=False,
            server_default="generic_table",
        ),
    )
    op.add_column(
        "statement_imports",
        sa.Column("parser_version", sa.String(length=24), nullable=False, server_default="1"),
    )


def downgrade():
    op.drop_column("statement_imports", "parser_version")
    op.drop_column("statement_imports", "parser_name")
    op.drop_column("statement_imports", "instrument_confidence")
