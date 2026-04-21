"""trip_flight_info_status

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-20
"""

from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    for col_name in ("flight_info", "flight_status"):
        if not _column_exists("trips", col_name):
            op.add_column("trips", sa.Column(col_name, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _column_exists("trips", "flight_status"):
        op.drop_column("trips", "flight_status")
    if _column_exists("trips", "flight_info"):
        op.drop_column("trips", "flight_info")
