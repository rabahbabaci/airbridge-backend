"""trip_latest_recommendation

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-21
"""

from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not _column_exists("trips", "latest_recommendation"):
        op.add_column(
            "trips", sa.Column("latest_recommendation", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    if _column_exists("trips", "latest_recommendation"):
        op.drop_column("trips", "latest_recommendation")
