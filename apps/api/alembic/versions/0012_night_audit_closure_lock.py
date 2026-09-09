"""Serialize Night Audit closure with financial posting.

Revision ID: 0012_night_audit_closure_lock
Revises: 0011_reservation_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_night_audit_closure_lock"
down_revision = "0011_reservation_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION hms_guard_financial_posting_business_date()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                state_current_date date;
                state_last_closed_at timestamp;
            BEGIN
                SELECT b.current_business_date, b.last_closed_at
                INTO state_current_date, state_last_closed_at
                FROM business_date_state AS b
                WHERE b.id = 1
                FOR UPDATE;

                IF NOT FOUND THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '55000',
                        MESSAGE = 'Business date is not initialized';
                END IF;

                IF NEW.business_date <> state_current_date THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = format(
                            'Financial posting date %s is not the current business date %s',
                            NEW.business_date,
                            state_current_date
                        );
                END IF;

                IF state_last_closed_at IS NOT NULL
                   AND state_last_closed_at::date >= NEW.business_date THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = format(
                            'Business date %s is closed for financial posting',
                            NEW.business_date
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
            CREATE TRIGGER trg_financial_transaction_business_date_guard
            BEFORE INSERT ON financial_transactions
            FOR EACH ROW
            EXECUTE FUNCTION hms_guard_financial_posting_business_date();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_financial_transaction_business_date_guard ON financial_transactions"
        )
    )
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS hms_guard_financial_posting_business_date()")
    )
