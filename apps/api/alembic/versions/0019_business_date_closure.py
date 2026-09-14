"""Fix Night Audit closure semantics.

Distinguish the hotel business date that was closed from the
physical timestamp at which Night Audit was performed.
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_business_date_closure"
down_revision = "0018_hotel_expenses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("business_date_state")
    }

    if "last_closed_business_date" not in columns:
        op.add_column(
            "business_date_state",
            sa.Column(
                "last_closed_business_date",
                sa.Date(),
                nullable=True,
            ),
        )

    # Existing staging state was written by the old implementation.
    # Once a close advanced current_business_date, the business date
    # that was closed was current_business_date - 1 day.
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE business_date_state
                SET last_closed_business_date =
                    current_business_date - INTERVAL '1 day'
                WHERE last_closed_at IS NOT NULL
                  AND last_closed_business_date IS NULL
                """
            )
        )

    elif bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                """
                UPDATE business_date_state
                SET last_closed_business_date =
                    date(current_business_date, '-1 day')
                WHERE last_closed_at IS NOT NULL
                  AND last_closed_business_date IS NULL
                """
            )
        )

    if bind.dialect.name == "postgresql":
        # Replace the existing function only.
        # The trigger already exists and must NOT be recreated.
        op.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION hms_guard_financial_posting_business_date()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    state_current_date date;
                    state_last_closed_business_date date;
                BEGIN
                    SELECT
                        b.current_business_date,
                        b.last_closed_business_date
                    INTO
                        state_current_date,
                        state_last_closed_business_date
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

                    IF state_last_closed_business_date IS NOT NULL
                       AND state_last_closed_business_date >= NEW.business_date THEN
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


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
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
                    SELECT
                        b.current_business_date,
                        b.last_closed_at
                    INTO
                        state_current_date,
                        state_last_closed_at
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

    op.drop_column(
        "business_date_state",
        "last_closed_business_date",
    )