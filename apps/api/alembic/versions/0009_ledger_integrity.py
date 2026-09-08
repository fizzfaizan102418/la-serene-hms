"""Enforce ledger integrity and immutability at the database boundary.

Revision ID: 0009_ledger_integrity
Revises: 0008_seed_business_date
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_ledger_integrity"
down_revision = "0008_seed_business_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite remains a legacy development/migration compatibility target.
        # Its limited ALTER TABLE support makes these DB-level constraints
        # impractical without rebuilding the tables.
        return

    op.create_check_constraint(
        "ck_ledger_entries_direction",
        "ledger_entries",
        "direction IN ('debit', 'credit')",
    )
    op.create_check_constraint(
        "ck_ledger_entries_amount_positive",
        "ledger_entries",
        "amount > 0",
    )
    op.create_check_constraint(
        "ck_financial_transactions_status",
        "financial_transactions",
        "status IN ('posted', 'reversed')",
    )
    op.create_check_constraint(
        "ck_financial_transactions_business_date",
        "financial_transactions",
        "business_date IS NOT NULL",
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION hms_guard_ledger_entry_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'Ledger entries are immutable; create a reversal instead';
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_ledger_entries_immutable
            BEFORE UPDATE OR DELETE ON ledger_entries
            FOR EACH ROW EXECUTE FUNCTION hms_guard_ledger_entry_mutation();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION hms_guard_financial_transaction_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Financial transactions are immutable; create a reversal instead';
                END IF;

                IF NEW.id <> OLD.id
                   OR NEW.transaction_no <> OLD.transaction_no
                   OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                   OR NEW.idempotency_fingerprint IS DISTINCT FROM OLD.idempotency_fingerprint
                   OR NEW.business_date <> OLD.business_date
                   OR NEW.transaction_type <> OLD.transaction_type
                   OR NEW.reference_type IS DISTINCT FROM OLD.reference_type
                   OR NEW.reference_id IS DISTINCT FROM OLD.reference_id
                   OR NEW.folio_id IS DISTINCT FROM OLD.folio_id
                   OR NEW.reservation_id IS DISTINCT FROM OLD.reservation_id
                   OR NEW.description <> OLD.description
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by
                   OR NEW.reversal_of_id IS DISTINCT FROM OLD.reversal_of_id
                   OR NEW.created_at <> OLD.created_at
                   OR NEW.updated_at <> OLD.updated_at
                THEN
                    RAISE EXCEPTION 'Financial transaction fields are immutable';
                END IF;

                IF OLD.status = 'reversed' AND NEW.status <> OLD.status THEN
                    RAISE EXCEPTION 'Reversed financial transactions cannot change status';
                END IF;

                IF OLD.status = 'posted' AND NEW.status NOT IN ('posted', 'reversed') THEN
                    RAISE EXCEPTION 'Invalid financial transaction status transition';
                END IF;

                RETURN NEW;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_financial_transactions_immutable
            BEFORE UPDATE OR DELETE ON financial_transactions
            FOR EACH ROW EXECUTE FUNCTION hms_guard_financial_transaction_mutation();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_financial_transactions_immutable ON financial_transactions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_financial_transaction_mutation()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_ledger_entries_immutable ON ledger_entries"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_ledger_entry_mutation()"))
    op.drop_constraint("ck_financial_transactions_business_date", "financial_transactions", type_="check")
    op.drop_constraint("ck_financial_transactions_status", "financial_transactions", type_="check")
    op.drop_constraint("ck_ledger_entries_amount_positive", "ledger_entries", type_="check")
    op.drop_constraint("ck_ledger_entries_direction", "ledger_entries", type_="check")
