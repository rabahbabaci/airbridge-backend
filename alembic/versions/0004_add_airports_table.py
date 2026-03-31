"""add_airports_table

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "airports" not in inspector.get_table_names():
        op.create_table(
            "airports",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("iata_code", sa.String(), nullable=False),
            sa.Column("icao_code", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("city", sa.String(), nullable=True),
            sa.Column("country", sa.String(), nullable=True),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("size_category", sa.String(), nullable=False),
            sa.Column("capability_tier", sa.Integer(), nullable=False, server_default="4"),
            sa.Column("has_live_tsa_feed", sa.Boolean(), server_default="false"),
            sa.Column("curb_to_checkin", sa.Integer(), nullable=True),
            sa.Column("checkin_to_security", sa.Integer(), nullable=True),
            sa.Column("security_to_gate", sa.Integer(), nullable=True),
            sa.Column("parking_to_terminal", sa.Integer(), nullable=True),
            sa.Column("transit_to_terminal", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_airports_iata_code", "airports", ["iata_code"], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "airports" in inspector.get_table_names():
        op.drop_table("airports")
