"""constrain account instrument type

Revision ID: f6bc8d2e475
Revises: e5ab7c9d364
"""

import sqlalchemy as sa
from alembic import op

revision = "f6bc8d2e475"
down_revision = "e5ab7c9d364"
branch_labels = None
depends_on = None

instrument_type_enum = sa.Enum(
    "bank",
    "current",
    "savings",
    "credit_card",
    name="accountinstrumenttype",
)


def upgrade():
    instrument_type_enum.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "accounts",
        "kind",
        existing_type=sa.String(length=24),
        type_=instrument_type_enum,
        existing_nullable=False,
        postgresql_using="kind::text::accountinstrumenttype",
    )


def downgrade():
    op.alter_column(
        "accounts",
        "kind",
        existing_type=instrument_type_enum,
        type_=sa.String(length=24),
        existing_nullable=False,
        postgresql_using="kind::text",
    )
    instrument_type_enum.drop(op.get_bind(), checkfirst=True)
