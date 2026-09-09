"""Guard balance-consuming financial operations against concurrent overspend.

Revision ID: 0010_financial_concurrency
Revises: 0009_ledger_integrity
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_financial_concurrency"
down_revision = "0009_ledger_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION hms_guard_folio_payment_balance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE available_balance numeric;
        BEGIN
            PERFORM 1 FROM folios WHERE id = NEW.folio_id FOR UPDATE;
            SELECT COALESCE(SUM(CASE WHEN le.direction = 'debit' THEN le.amount ELSE -le.amount END), 0)
            INTO available_balance
            FROM ledger_entries le
            JOIN financial_transactions ft ON ft.id = le.transaction_id
            WHERE le.folio_id = NEW.folio_id
              AND le.account = 'Guest Receivables'
              AND ft.status = 'posted';
            IF NEW.amount > available_balance THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = format('Payment exceeds outstanding balance of %s', available_balance);
            END IF;
            RETURN NEW;
        END;
        $$;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_folio_payment_balance_guard
        BEFORE INSERT ON payments
        FOR EACH ROW EXECUTE FUNCTION hms_guard_folio_payment_balance();
    """))

    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION hms_guard_payment_refund_balance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE payment_amount numeric; refunded_amount numeric;
        BEGIN
            SELECT p.amount INTO payment_amount
            FROM payments AS p
            WHERE p.id = NEW.payment_id
            FOR UPDATE;
            IF payment_amount IS NULL THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23503',
                    MESSAGE = 'Refund references a missing payment';
            END IF;
            SELECT COALESCE(SUM(pr.amount), 0) INTO refunded_amount
            FROM payment_refunds AS pr
            WHERE pr.payment_id = NEW.payment_id;
            IF refunded_amount + NEW.amount > payment_amount THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = format('Refund exceeds refundable payment balance of %s', payment_amount - refunded_amount);
            END IF;
            RETURN NEW;
        END;
        $$;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_payment_refund_balance_guard
        BEFORE INSERT ON payment_refunds
        FOR EACH ROW EXECUTE FUNCTION hms_guard_payment_refund_balance();
    """))

    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION hms_guard_deposit_balance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE stay_balance numeric; signed_amount numeric; required_amount numeric;
        BEGIN
            PERFORM 1 FROM stays WHERE id = NEW.stay_id FOR UPDATE;
            SELECT COALESCE(SUM(CASE
                WHEN dt.transaction_type IN ('received', 'adjusted', 'transferred_in') THEN dt.amount
                WHEN dt.transaction_type IN ('applied', 'refunded', 'transferred_out') THEN -dt.amount
                ELSE 0 END), 0)
            INTO stay_balance
            FROM deposit_transactions AS dt
            WHERE dt.stay_id = NEW.stay_id;
            signed_amount := CASE
                WHEN NEW.transaction_type IN ('received', 'adjusted', 'transferred_in') THEN NEW.amount
                WHEN NEW.transaction_type IN ('applied', 'refunded', 'transferred_out') THEN -NEW.amount
                ELSE 0 END;
            IF stay_balance + signed_amount < 0 THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'Deposit transaction exceeds available deposit balance';
            END IF;
            IF NEW.transaction_type = 'received' THEN
                SELECT s.deposit_required INTO required_amount
                FROM stays AS s
                WHERE s.id = NEW.stay_id;
                IF required_amount IS NOT NULL AND required_amount > 0
                   AND stay_balance + signed_amount > required_amount THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = 'Deposit received exceeds required deposit';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """))
    op.execute(sa.text("""
        CREATE TRIGGER trg_deposit_balance_guard
        BEFORE INSERT ON deposit_transactions
        FOR EACH ROW EXECUTE FUNCTION hms_guard_deposit_balance();
    """))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_deposit_balance_guard ON deposit_transactions"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_deposit_balance()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_payment_refund_balance_guard ON payment_refunds"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_payment_refund_balance()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_folio_payment_balance_guard ON payments"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_folio_payment_balance()"))
