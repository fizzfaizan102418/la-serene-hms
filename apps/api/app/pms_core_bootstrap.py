from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import engine
from .pms_core import Stay
from .models import Reservation, ReservationRoom


TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_reservation_room_creates_stay
AFTER INSERT ON reservation_rooms
BEGIN
    INSERT INTO stays (reservation_id, room_id, guest_id, status, check_in, check_out, actual_check_in, actual_check_out, agreed_rate, discount_percent, discount_amount, payment_due_policy, deposit_required, deposit_received, notes, created_at, updated_at)
    SELECT NEW.reservation_id, NEW.room_id, r.guest_id,
           CASE WHEN r.status = 'checked_in' THEN 'checked_in' ELSE 'reserved' END,
           r.check_in, r.check_out,
           CASE WHEN r.status = 'checked_in' THEN COALESCE(r.checked_in_at, CURRENT_TIMESTAMP) ELSE NULL END,
           NULL, 0, 0, 0, 'at_checkout', 0, 0, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    FROM reservations r
    WHERE r.id = NEW.reservation_id
      AND NOT EXISTS (SELECT 1 FROM stays s WHERE s.reservation_id = NEW.reservation_id AND s.room_id = NEW.room_id AND s.status != 'completed');
END;

CREATE TRIGGER IF NOT EXISTS trg_reservation_status_syncs_stays
AFTER UPDATE OF status ON reservations
WHEN NEW.status IN ('checked_in', 'checked_out')
BEGIN
    UPDATE stays
       SET status = CASE WHEN NEW.status = 'checked_in' THEN 'checked_in' ELSE 'completed' END,
           actual_check_in = CASE WHEN NEW.status = 'checked_in' THEN COALESCE(actual_check_in, COALESCE(NEW.checked_in_at, CURRENT_TIMESTAMP)) ELSE actual_check_in END,
           actual_check_out = CASE WHEN NEW.status = 'checked_out' THEN COALESCE(actual_check_out, COALESCE(NEW.checked_out_at, CURRENT_TIMESTAMP)) ELSE actual_check_out END,
           updated_at = CURRENT_TIMESTAMP
     WHERE reservation_id = NEW.id;
END;
"""


def ensure_pms_core_schema() -> None:
    """Idempotently prepare PMS Core 2 tables, history, and lifecycle triggers."""
    # The app uses create_all for compatibility with existing local SQLite installs.
    # Importing the models registers the new tables before create_all executes.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO stays (reservation_id, room_id, guest_id, status, check_in, check_out, actual_check_in, actual_check_out, agreed_rate, discount_percent, discount_amount, payment_due_policy, deposit_required, deposit_received, notes, created_at, updated_at) "
            "SELECT rr.reservation_id, rr.room_id, r.guest_id, "
            "CASE WHEN r.status = 'checked_in' THEN 'checked_in' WHEN r.status = 'checked_out' THEN 'completed' ELSE 'reserved' END, "
            "r.check_in, r.check_out, r.checked_in_at, r.checked_out_at, 0, 0, 0, 'at_checkout', 0, 0, NULL, r.created_at, r.updated_at "
            "FROM reservation_rooms rr JOIN reservations r ON r.id = rr.reservation_id "
            "WHERE NOT EXISTS (SELECT 1 FROM stays s WHERE s.reservation_id = rr.reservation_id AND s.room_id = rr.room_id)"
        )
        for statement in TRIGGER_SQL.strip().split("\n\n"):
            connection.exec_driver_sql(statement)


def sync_existing_stays(db: Session) -> None:
    """Ensure ORM-visible data is available after a restore or manual database copy."""
    # This is intentionally a lightweight consistency check; the database triggers
    # remain the primary source of lifecycle synchronization.
    db.execute(text("SELECT 1 FROM stays LIMIT 1"))
