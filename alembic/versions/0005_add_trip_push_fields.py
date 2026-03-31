"""add_trip_push_fields

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("trips")]

    if "last_pushed_leave_home_at" not in columns:
        op.add_column(
            "trips",
            sa.Column("last_pushed_leave_home_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "trip_status" not in columns:
        op.add_column(
            "trips",
            sa.Column("trip_status", sa.String(), nullable=False, server_default="created"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("trips")]

    if "trip_status" in columns:
        op.drop_column("trips", "trip_status")
    if "last_pushed_leave_home_at" in columns:
        op.drop_column("trips", "last_pushed_leave_home_at")
