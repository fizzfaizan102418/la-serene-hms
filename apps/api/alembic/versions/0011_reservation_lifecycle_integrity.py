"""Enforce reservation dates and lifecycle transitions in PostgreSQL.

Revision ID: 0011_reservation_lifecycle
Revises: 0010_ledger_balance_integrity
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_reservation_lifecycle"
down_revision = "0010_ledger_balance_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            """
            ALTER TABLE reservations
            ADD CONSTRAINT ck_reservations_dates
            CHECK (check_out > check_in)
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION hms_guard_reservation_lifecycle()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.status NOT IN ('reserved', 'checked_in', 'checked_out', 'cancelled', 'no_show') THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = format('Invalid reservation status: %s', NEW.status);
                END IF;

                IF NEW.status = OLD.status THEN
                    RETURN NEW;
                END IF;

                IF OLD.status = 'reserved' AND NEW.status IN ('checked_in', 'cancelled', 'no_show') THEN
                    RETURN NEW;
                END IF;

                IF OLD.status = 'checked_in' AND NEW.status = 'checked_out' THEN
                    RETURN NEW;
                END IF;

                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = format(
                        'Invalid reservation status transition: %s -> %s',
                        OLD.status,
                        NEW.status
                    );
                RETURN NEW;
            END;
            $$;
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_reservation_lifecycle_integrity
            BEFORE UPDATE OF status ON reservations
            FOR EACH ROW
            EXECUTE FUNCTION hms_guard_reservation_lifecycle();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_reservation_lifecycle_integrity ON reservations"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS hms_guard_reservation_lifecycle()"))
    op.execute(
        sa.text(
            "ALTER TABLE reservations DROP CONSTRAINT IF EXISTS ck_reservations_dates"
        )
    )
