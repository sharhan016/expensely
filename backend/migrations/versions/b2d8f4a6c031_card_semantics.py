"""add card semantics and classification evidence

Revision ID: b2d8f4a6c031
Revises: a1c7e3f5b920
"""

import sqlalchemy as sa
from alembic import op

revision = "b2d8f4a6c031"
down_revision = "a1c7e3f5b920"
branch_labels = None
depends_on = None


def upgrade():
    for table_name in ("import_rows", "transaction_meanings"):
        op.add_column(
            table_name,
            sa.Column(
                "category", sa.String(length=40), nullable=False, server_default="uncategorized"
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "category_confidence",
                sa.String(length=16),
                nullable=False,
                server_default="unresolved",
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "classification_evidence",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "decision_source",
                sa.String(length=32),
                nullable=False,
                server_default="unresolved",
            ),
        )


def downgrade():
    for table_name in ("transaction_meanings", "import_rows"):
        op.drop_column(table_name, "decision_source")
        op.drop_column(table_name, "classification_evidence")
        op.drop_column(table_name, "category_confidence")
        op.drop_column(table_name, "category")
