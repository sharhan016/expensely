"""add counterparty flow identity

Revision ID: 6e4b1a9c2d77
Revises: 18c9c7880d25
"""

import sqlalchemy as sa
from alembic import op

revision = "6e4b1a9c2d77"
down_revision = "18c9c7880d25"
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
        "counterparties",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("default_purpose", purpose, nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_counterparties_user_id", "counterparties", ["user_id"])
    op.create_table(
        "counterparty_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("counterparty_id", sa.Integer(), nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.Column("alias_value", sa.String(length=180), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparties.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "alias_type",
            "alias_value",
            name="uq_counterparty_alias_owner_type_value",
        ),
    )
    op.create_index(
        "ix_counterparty_aliases_counterparty_id", "counterparty_aliases", ["counterparty_id"]
    )
    op.create_index("ix_counterparty_aliases_user_id", "counterparty_aliases", ["user_id"])
    op.add_column("import_rows", sa.Column("counterparty_id", sa.Integer(), nullable=True))
    op.add_column(
        "import_rows",
        sa.Column("counterparty_name", sa.String(length=160), nullable=False, server_default=""),
    )
    op.add_column(
        "import_rows",
        sa.Column(
            "counterparty_alias_type", sa.String(length=32), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "import_rows",
        sa.Column(
            "counterparty_alias_value",
            sa.String(length=180),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "import_rows",
        sa.Column(
            "counterparty_confidence",
            sa.String(length=16),
            nullable=False,
            server_default="unresolved",
        ),
    )
    op.create_foreign_key(
        "fk_import_rows_counterparty_id",
        "import_rows",
        "counterparties",
        ["counterparty_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("transaction_meanings", sa.Column("counterparty_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_transaction_meanings_counterparty_id",
        "transaction_meanings",
        "counterparties",
        ["counterparty_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_transaction_meanings_counterparty_id",
        "transaction_meanings",
        ["counterparty_id"],
    )


def downgrade():
    op.drop_index("ix_transaction_meanings_counterparty_id", table_name="transaction_meanings")
    op.drop_constraint(
        "fk_transaction_meanings_counterparty_id", "transaction_meanings", type_="foreignkey"
    )
    op.drop_column("transaction_meanings", "counterparty_id")
    op.drop_constraint("fk_import_rows_counterparty_id", "import_rows", type_="foreignkey")
    op.drop_column("import_rows", "counterparty_confidence")
    op.drop_column("import_rows", "counterparty_alias_value")
    op.drop_column("import_rows", "counterparty_alias_type")
    op.drop_column("import_rows", "counterparty_name")
    op.drop_column("import_rows", "counterparty_id")
    op.drop_index("ix_counterparty_aliases_user_id", table_name="counterparty_aliases")
    op.drop_index("ix_counterparty_aliases_counterparty_id", table_name="counterparty_aliases")
    op.drop_table("counterparty_aliases")
    op.drop_index("ix_counterparties_user_id", table_name="counterparties")
    op.drop_table("counterparties")
