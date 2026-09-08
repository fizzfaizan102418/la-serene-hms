"""Seed the initial hotel business date.

Revision ID: 0008_seed_business_date
Revises: 0007_financial_idempotency
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_seed_business_date"
down_revision = "0007_financial_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw migration SQL does not invoke SQLAlchemy ORM defaults. SQLite also
    # cannot rely on the model's Python-side default for this INSERT, so every
    # required timestamp column is supplied explicitly.
    op.execute(
        sa.text(
            "INSERT INTO business_date_state "
            "(id, current_business_date, opened_at, updated_at) "
            "SELECT 1, CURRENT_DATE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM business_date_state)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM business_date_state WHERE id = 1"))
