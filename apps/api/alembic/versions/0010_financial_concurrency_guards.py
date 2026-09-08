"""Guard balance-consuming financial operations against concurrent overspend.

Revision ID: 0010_financial_concurrency_guards
Revises: 0009_ledger_integrity
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_financial_concurrency_guards"
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
            CREATE OR REPLACE FUNCTION hms_guard_payment_refund_balance()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                payment_amount numeric;
                refunded_amount numeric;
            BEGIN
                SELECT amount INTO payment_amount
                FROM payments
                WHERE id = NEW.payment_id
                FOR UPDATE;

                IF payment_amount IS NULL THEN
                    RAISE EXCEPTION 'Refund references a missing payment';
                END IF;

                SELECT COALESCE(SUM(amount), 0)
                INTO refunded_amount
                FROM payment_refunds
                WHERE payment_id = NEW.payment_id;

                IF refunded_amount > payment_amount THEN
                    RAISE EXCEPTION 'Refund exceeds refundable payment balance of %', payment_amount;
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
            CREATE TRIGGER trg_payment_refund_balance_guard
            BEFORE INSERT ON payment_refunds
            FOR EACH ROW EXECUTE FUNCTION hms_guard_payment_refund_balance();
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION hms_guard_deposit_balance()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                stay_balance numeric;
                signed_amount numeric;
                deposit_required numeric;
            BEGIN
                SELECT COALESCE(SUM(
                    CASE
                        WHEN transaction_type IN ('received', 'adjusted', 'transferred_in') THEN amount
                        WHEN transaction_type IN ('applied', 'refunded', 'transferred_out') THEN -amount
                        ELSE 0
                    END
                ), 0)
                INTO stay_balance
                FROM deposit_transactions
                WHERE stay_id = NEW.stay_id;

                signed_amount := CASE
                    WHEN NEW.transaction_type IN ('received', 'adjusted', 'transferred_in') THEN NEW.amount
                    WHEN NEW.transaction_type IN ('applied', 'refunded', 'transferred_out') THEN -NEW.amount
                    ELSE 0
                END;

                IF stay_balance + signed_amount < 0 THEN
                    RAISE EXCEPTION 'Deposit transaction exceeds available deposit balance';
                END IF;

                IF NEW.transaction_type = 'received' THEN
                    SELECT deposit_required INTO deposit_required
                    FROM stays
                    WHERE id = NEW.stay_id
                    FOR UPDATE;

                    IF deposit_required IS NOT NULL
                       AND deposit_required > 0
                       AND stay_balance + signed_amount > deposit_required
                    THEN
                        RAISE EXCEPTION 'Deposit received exceeds required deposit';
                    END IF;
                ELSE
                    PERFORM 1 FROM stays WHERE id = NEW.stay_id FOR UPDATE;
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
            CREATE TRIGGER trg_deposit_balance_guard
            BEFORE INSERT ON deposit_transactions
            FOR EACH ROW EXECUTE FUNCTION hms_guard_deposit_balance();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_deposit_balance_guard ON deposit_transactions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_deposit_balance()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_refund_balance_guard ON payment_refunds"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_payment_refund_balance()"))
