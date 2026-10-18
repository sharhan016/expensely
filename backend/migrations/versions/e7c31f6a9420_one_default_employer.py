"""enforce one default employer per user

Revision ID: e7c31f6a9420
Revises: d53eb029a932
"""

import sqlalchemy as sa
from alembic import op

revision = "e7c31f6a9420"
down_revision = "d53eb029a932"
branch_labels = None
depends_on = None

INDEX_NAME = "ux_employers_one_default_per_user"


def upgrade():
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM employers
                WHERE is_default
                GROUP BY user_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot enforce one default employer: duplicate defaults exist';
            END IF;
        END
        $$
        """
    )
    op.create_index(
        INDEX_NAME,
        "employers",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="employers")
