"""add_trip_push_count

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("trips")]

    if "push_count" not in columns:
        op.add_column(
            "trips",
            sa.Column("push_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("trips")]

    if "push_count" in columns:
        op.drop_column("trips", "push_count")
