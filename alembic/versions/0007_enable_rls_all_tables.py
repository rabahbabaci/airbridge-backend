"""enable_rls_all_tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-01
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLES = [
    "users",
    "trips",
    "recommendations",
    "device_tokens",
    "feedback",
    "events",
    "airports",
    "alembic_version",
]


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY deny_anon_access ON public.{table} "
            f"FOR ALL TO anon USING (false);"
        )
        op.execute(
            f"CREATE POLICY deny_authenticated_access ON public.{table} "
            f"FOR ALL TO authenticated USING (false);"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS deny_anon_access ON public.{table};")
        op.execute(
            f"DROP POLICY IF EXISTS deny_authenticated_access ON public.{table};"
        )
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY;")
