"""feedback_tracking_columns

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-04
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Task 5: TSA observations table
    op.create_table(
        "tsa_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("airport_code", sa.String(), nullable=False, index=True),
        sa.Column("checkpoint_type", sa.String(), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("time_of_day", sa.Integer(), nullable=False),
        sa.Column("wait_minutes", sa.Integer(), nullable=False),
        sa.Column("reported_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
    )

    # Task 8: Passive tracking columns on trips
    op.add_column("trips", sa.Column("projected_timeline", sa.JSON(), nullable=True))
    op.add_column("trips", sa.Column("actual_depart_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trips", sa.Column("auto_completed", sa.Boolean(), server_default="false"))
    op.add_column("trips", sa.Column("feedback_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("trips", "feedback_requested_at")
    op.drop_column("trips", "auto_completed")
    op.drop_column("trips", "actual_depart_at")
    op.drop_column("trips", "projected_timeline")
    op.drop_table("tsa_observations")
