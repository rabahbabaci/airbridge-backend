"""feedback_tracking_columns

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-04
"""

from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    return table_name in insp.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    # Task 5: TSA observations table
    if not _table_exists("tsa_observations"):
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
    for col_name, col_type in [
        ("projected_timeline", sa.JSON()),
        ("actual_depart_at", sa.DateTime(timezone=True)),
        ("auto_completed", sa.Boolean()),
        ("feedback_requested_at", sa.DateTime(timezone=True)),
    ]:
        if not _column_exists("trips", col_name):
            kwargs = {}
            if col_name == "auto_completed":
                kwargs["server_default"] = "false"
            op.add_column("trips", sa.Column(col_name, col_type, nullable=True, **kwargs))


def downgrade() -> None:
    op.drop_column("trips", "feedback_requested_at")
    op.drop_column("trips", "auto_completed")
    op.drop_column("trips", "actual_depart_at")
    op.drop_column("trips", "projected_timeline")
    op.drop_table("tsa_observations")
