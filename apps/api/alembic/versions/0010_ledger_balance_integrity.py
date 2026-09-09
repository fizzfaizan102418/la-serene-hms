"""Enforce balanced financial transactions at the PostgreSQL boundary.

Revision ID: 0010_ledger_balance_integrity
Revises: 0009_ledger_integrity
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_ledger_balance_integrity"
down_revision = "0009_ledger_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION hms_guard_financial_transaction_balance()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                entry_count integer;
                debit_total numeric;
                credit_total numeric;
                currency_count integer;
            BEGIN
                SELECT
                    COUNT(*)::integer,
                    COALESCE(SUM(CASE WHEN le.direction = 'debit' THEN le.amount ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN le.direction = 'credit' THEN le.amount ELSE 0 END), 0),
                    COUNT(DISTINCT le.currency)::integer
                INTO entry_count, debit_total, credit_total, currency_count
                FROM ledger_entries AS le
                WHERE le.transaction_id = NEW.id;

                IF entry_count < 2 THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'Financial transactions require at least two ledger entries';
                END IF;

                IF currency_count <> 1 THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'Financial transactions must use exactly one currency';
                END IF;

                IF debit_total <> credit_total THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = format(
                            'Financial transaction is unbalanced: debit=%s credit=%s',
                            debit_total,
                            credit_total
                        );
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
            CREATE CONSTRAINT TRIGGER trg_financial_transaction_balance_on_transaction
            AFTER INSERT ON financial_transactions
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION hms_guard_financial_transaction_balance();
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE CONSTRAINT TRIGGER trg_financial_transaction_balance_on_entry
            AFTER INSERT ON ledger_entries
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION hms_guard_financial_transaction_balance();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_financial_transaction_balance_on_entry ON ledger_entries"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_financial_transaction_balance_on_transaction ON financial_transactions"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_financial_transaction_balance()"))
