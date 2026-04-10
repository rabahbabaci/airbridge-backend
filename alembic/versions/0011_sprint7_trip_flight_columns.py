"""sprint7_trip_flight_columns

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-09
"""

from alembic import op
from sqlalchemy import inspect
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    insp = inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    for col_name in ("origin_iata", "destination_iata", "airline"):
        if not _column_exists("trips", col_name):
            op.add_column("trips", sa.Column(col_name, sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("trips", "airline")
    op.drop_column("trips", "destination_iata")
    op.drop_column("trips", "origin_iata")
