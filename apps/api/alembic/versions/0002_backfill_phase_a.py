"""Backfill normalized Phase A PMS records.

Revision ID: 0002_backfill_phase_a
Revises: 0001_initial_hms
Create Date: 2026-09-08
"""
from alembic import op

revision = "0002_backfill_phase_a"
down_revision = "0001_initial_hms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite development installs receive compatible runtime backfills from
        # the existing startup compatibility layer.
        return

    bind.exec_driver_sql(
        "INSERT INTO stay_occupants (stay_id, guest_id, role, is_primary, check_in, check_out, notes, created_at, updated_at) "
        "SELECT s.id, s.guest_id, 'primary', TRUE, s.check_in, s.check_out, s.notes, s.created_at, s.updated_at "
        "FROM stays s WHERE s.guest_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM stay_occupants o WHERE o.stay_id = s.id AND o.is_primary = TRUE)"
    )
    bind.exec_driver_sql(
        "INSERT INTO stay_rate_segments (stay_id, from_date, to_date, rate, discount_percent, discount_amount, rate_plan, source, notes, created_at, updated_at) "
        "SELECT s.id, s.check_in, s.check_out, s.agreed_rate + s.discount_amount, s.discount_percent, s.discount_amount, NULL, 'migration', s.notes, s.created_at, s.updated_at "
        "FROM stays s WHERE s.check_out > s.check_in "
        "AND NOT EXISTS (SELECT 1 FROM stay_rate_segments r WHERE r.stay_id = s.id)"
    )
    bind.exec_driver_sql(
        "INSERT INTO business_date_state (id, current_business_date, opened_at, last_closed_at, updated_at) "
        "VALUES (1, CURRENT_DATE, CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP) "
        "ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    # Deliberately non-destructive. Normalized history must not be silently deleted.
    pass
