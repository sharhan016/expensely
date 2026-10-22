"""review period, acknowledged gaps and workflow runs with events

Revision ID: d1a7b3c5e902
Revises: c9f1a2b3d4e5
"""

import sqlalchemy as sa
from alembic import op

revision = "d1a7b3c5e902"
down_revision = "c9f1a2b3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_periods_user_id", "review_periods", ["user_id"], unique=True)
    op.create_table(
        "acknowledged_gaps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE")),
        sa.Column("gap_start", sa.Date(), nullable=False),
        sa.Column("gap_end", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "gap_start", "gap_end"),
    )
    op.create_index("ix_acknowledged_gaps_user_id", "acknowledged_gaps", ["user_id"])
    op.create_index("ix_acknowledged_gaps_account_id", "acknowledged_gaps", ["account_id"])
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_stage", sa.String(length=32), nullable=False),
        sa.Column("waiting_for", sa.JSON(), nullable=True),
        sa.Column("import_ids", sa.JSON(), nullable=False),
        sa.Column("client_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "client_key"),
    )
    op.create_index("ix_workflow_runs_user_id", "workflow_runs", ["user_id"])
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("machine_status", sa.String(length=16), nullable=False),
        sa.Column("step_key", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "step_key"),
    )
    op.create_index("ix_workflow_events_run_id", "workflow_events", ["run_id"])
    op.create_index("ix_workflow_events_user_id", "workflow_events", ["user_id"])


def downgrade():
    op.drop_table("workflow_events")
    op.drop_table("workflow_runs")
    op.drop_table("acknowledged_gaps")
    op.drop_table("review_periods")
