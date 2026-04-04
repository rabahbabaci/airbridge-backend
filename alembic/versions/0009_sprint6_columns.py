"""sprint6_columns

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-04
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Task 1: Stripe
    op.add_column("users", sa.Column("stripe_customer_id", sa.String(), nullable=True))

    # Task 3: Morning email
    op.add_column("trips", sa.Column("morning_email_sent_at", sa.DateTime(timezone=True), nullable=True))

    # Task 4: SMS escalation
    op.add_column("trips", sa.Column("time_to_go_push_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trips", sa.Column("sms_count", sa.Integer(), nullable=True, server_default="0"))


def downgrade() -> None:
    op.drop_column("trips", "sms_count")
    op.drop_column("trips", "time_to_go_push_sent_at")
    op.drop_column("trips", "morning_email_sent_at")
    op.drop_column("users", "stripe_customer_id")
