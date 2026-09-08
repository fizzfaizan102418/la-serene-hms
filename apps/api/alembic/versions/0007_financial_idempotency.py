"""Add idempotency keys to financial transactions.

Revision ID: 0007_financial_idempotency
Revises: 0006_phase_a_folio_item_routing
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_financial_idempotency"
down_revision = "0006_phase_a_folio_item_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("financial_transactions", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.create_index("ix_financial_transactions_idempotency_key", "financial_transactions", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_financial_transactions_idempotency_key", table_name="financial_transactions")
    op.drop_column("financial_transactions", "idempotency_key")
