import unittest
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Guest, Reservation


class PostgreSQLReservationLifecycleIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if engine.dialect.name != "postgresql":
            raise unittest.SkipTest("HMS_DATABASE_URL is not PostgreSQL")

    def test_reservation_status_transitions_are_constrained(self):
        with Session(engine) as db:
            guest = Guest(full_name="Lifecycle Test Guest")
            db.add(guest)
            db.flush()
            reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 10), check_out=date(2026, 9, 12), status="reserved")
            db.add(reservation)
            db.flush()
            reservation_id = reservation.id
            guest_id = guest.id
            db.commit()

        with Session(engine) as db:
            reservation = db.get(Reservation, reservation_id)
            reservation.status = "checked_in"
            db.commit()
            reservation = db.get(Reservation, reservation_id)
            reservation.status = "checked_out"
            db.commit()
            reservation = db.get(Reservation, reservation_id)
            reservation.status = "reserved"
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()
            db.execute(text("DELETE FROM reservations WHERE id = :id"), {"id": reservation_id})
            db.execute(text("DELETE FROM guests WHERE id = :id"), {"id": guest_id})
            db.commit()

    def test_terminal_reservation_status_cannot_be_reopened(self):
        with Session(engine) as db:
            guest = Guest(full_name="Terminal Lifecycle Test Guest")
            db.add(guest)
            db.flush()
            reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 10), check_out=date(2026, 9, 12), status="reserved")
            db.add(reservation)
            db.flush()
            reservation.status = "no_show"
            db.commit()
            reservation_id = reservation.id
            guest_id = guest.id

        with Session(engine) as db:
            reservation = db.get(Reservation, reservation_id)
            reservation.status = "checked_in"
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()
            db.execute(text("DELETE FROM reservations WHERE id = :id"), {"id": reservation_id})
            db.execute(text("DELETE FROM guests WHERE id = :id"), {"id": guest_id})
            db.commit()

    def test_reservation_dates_must_be_valid(self):
        with Session(engine) as db:
            guest = Guest(full_name="Date Constraint Test Guest")
            db.add(guest)
            db.flush()
            db.add(Reservation(guest_id=guest.id, check_in=date(2026, 9, 12), check_out=date(2026, 9, 12), status="reserved"))
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()


if __name__ == "__main__":
    unittest.main()
