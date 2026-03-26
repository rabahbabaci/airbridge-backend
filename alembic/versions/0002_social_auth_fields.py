"""social_auth_fields

Revision ID: 0002
Revises:
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "phone_number", existing_type=sa.String(), nullable=True)
    op.add_column("users", sa.Column("auth_provider", sa.String(), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(), nullable=True))
    op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "email")
    op.drop_column("users", "display_name")
    op.drop_column("users", "auth_provider")
    op.alter_column("users", "phone_number", existing_type=sa.String(), nullable=False)
