"""add_apple_user_id

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("apple_user_id", sa.String(), nullable=True))
    op.create_unique_constraint("uq_users_apple_user_id", "users", ["apple_user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_users_apple_user_id", "users", type_="unique")
    op.drop_column("users", "apple_user_id")
