"""Add idempotency metadata to financial transactions.

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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("financial_transactions")}
    indexes = {index["name"] for index in inspector.get_indexes("financial_transactions")}

    # 0001 bootstraps from current ORM metadata, so fresh databases may already
    # contain columns introduced by later model revisions. Older databases may
    # not. Keep this revision safe for both cases.
    if "idempotency_key" not in columns:
        op.add_column(
            "financial_transactions",
            sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        )
    if "idempotency_fingerprint" not in columns:
        op.add_column(
            "financial_transactions",
            sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
        )

    if "ix_financial_transactions_idempotency_key" not in indexes:
        op.create_index(
            "ix_financial_transactions_idempotency_key",
            "financial_transactions",
            ["idempotency_key"],
            unique=True,
        )
    if "ix_financial_transactions_idempotency_fingerprint" not in indexes:
        op.create_index(
            "ix_financial_transactions_idempotency_fingerprint",
            "financial_transactions",
            ["idempotency_fingerprint"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("financial_transactions")}
    if "ix_financial_transactions_idempotency_fingerprint" in indexes:
        op.drop_index(
            "ix_financial_transactions_idempotency_fingerprint",
            table_name="financial_transactions",
        )
    if "ix_financial_transactions_idempotency_key" in indexes:
        op.drop_index(
            "ix_financial_transactions_idempotency_key",
            table_name="financial_transactions",
        )

    columns = {column["name"] for column in inspector.get_columns("financial_transactions")}
    if "idempotency_fingerprint" in columns:
        op.drop_column("financial_transactions", "idempotency_fingerprint")
    if "idempotency_key" in columns:
        op.drop_column("financial_transactions", "idempotency_key")
