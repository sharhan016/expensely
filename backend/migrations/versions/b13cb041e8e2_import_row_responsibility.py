"""add responsibility fields to staged rows

Revision ID: b13cb041e8e2
Revises: a85f53d4e291
"""

import sqlalchemy as sa
from alembic import op

revision = "b13cb041e8e2"
down_revision = "a85f53d4e291"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("import_rows", sa.Column("person_id", sa.Integer(), nullable=True))
    op.add_column("import_rows", sa.Column("employer_id", sa.Integer(), nullable=True))
    op.add_column("import_rows", sa.Column("trip_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_import_rows_person", "import_rows", "people", ["person_id"], ["id"])
    op.create_foreign_key(
        "fk_import_rows_employer", "import_rows", "employers", ["employer_id"], ["id"]
    )
    op.create_foreign_key("fk_import_rows_trip", "import_rows", "trips", ["trip_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_import_rows_trip", "import_rows", type_="foreignkey")
    op.drop_constraint("fk_import_rows_employer", "import_rows", type_="foreignkey")
    op.drop_constraint("fk_import_rows_person", "import_rows", type_="foreignkey")
    op.drop_column("import_rows", "trip_id")
    op.drop_column("import_rows", "employer_id")
    op.drop_column("import_rows", "person_id")
