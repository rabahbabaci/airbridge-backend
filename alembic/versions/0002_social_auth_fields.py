"""social_auth_fields

Revision ID: 0002
Revises:
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = None
branch_labels = None
depends_on = None


def _existing_columns():
    conn = op.get_bind()
    inspector = inspect(conn)
    return [c["name"] for c in inspector.get_columns("users")]


def upgrade() -> None:
    columns = _existing_columns()

    op.alter_column("users", "phone_number", existing_type=sa.String(), nullable=True)

    if "auth_provider" not in columns:
        op.add_column("users", sa.Column("auth_provider", sa.String(), nullable=True))
    if "display_name" not in columns:
        op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    if "email" not in columns:
        op.add_column("users", sa.Column("email", sa.String(), nullable=True))

    # Only create constraint if email column didn't already have one
    conn = op.get_bind()
    inspector = inspect(conn)
    unique_constraints = inspector.get_unique_constraints("users")
    existing_uq_names = [c["name"] for c in unique_constraints]
    if "uq_users_email" not in existing_uq_names:
        op.create_unique_constraint("uq_users_email", "users", ["email"])


def downgrade() -> None:
    columns = _existing_columns()

    conn = op.get_bind()
    inspector = inspect(conn)
    unique_constraints = inspector.get_unique_constraints("users")
    existing_uq_names = [c["name"] for c in unique_constraints]
    if "uq_users_email" in existing_uq_names:
        op.drop_constraint("uq_users_email", "users", type_="unique")

    if "email" in columns:
        op.drop_column("users", "email")
    if "display_name" in columns:
        op.drop_column("users", "display_name")
    if "auth_provider" in columns:
        op.drop_column("users", "auth_provider")

    op.alter_column("users", "phone_number", existing_type=sa.String(), nullable=False)
