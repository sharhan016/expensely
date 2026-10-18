"""add classification rules

Revision ID: a85f53d4e291
Revises: 64c48bd3cd77
"""

import sqlalchemy as sa
from alembic import op

revision = "a85f53d4e291"
down_revision = "64c48bd3cd77"
branch_labels = None
depends_on = None


def upgrade():
    purpose = sa.Enum(
        "PERSONAL",
        "BUSINESS",
        "INCOME",
        "TRANSFER",
        "LOAN_GIVEN",
        "LOAN_REPAYMENT",
        "ENTRUSTED",
        "REIMBURSEMENT",
        "REFUND",
        "OTHER",
        name="purpose",
        create_type=False,
    )
    op.create_table(
        "classification_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=140), nullable=False),
        sa.Column("description_contains", sa.String(length=180), nullable=False),
        sa.Column("purpose", purpose, nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=True),
        sa.Column("employer_id", sa.Integer(), nullable=True),
        sa.Column("min_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("max_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["employer_id"], ["employers.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_classification_rules_user_id", "classification_rules", ["user_id"])


def downgrade():
    op.drop_index("ix_classification_rules_user_id", table_name="classification_rules")
    op.drop_table("classification_rules")
