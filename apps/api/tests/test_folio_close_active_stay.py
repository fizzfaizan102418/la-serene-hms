import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.billing import close_folio
from app.models import Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.pms_core import Stay


class FolioCloseActiveStayTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)

        role = Role(name="admin")
        user = User(username="admin", password_hash="test", role_id=1)
        guest = Guest(full_name="Folio Guest")
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add(role)
        self.db.flush()
        user.role_id = role.id
        self.db.add_all([user, guest, room_type])
        self.db.flush()

        room = Room(number="301", room_type_id=room_type.id, status="occupied")
        reservation = Reservation(
            guest_id=guest.id,
            check_in=date(2026, 9, 10),
            check_out=date(2026, 9, 11),
            status="checked_in",
        )
        self.db.add_all([room, reservation])
        self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.flush()
        stay = Stay(
            reservation_id=reservation.id,
            room_id=room.id,
            guest_id=guest.id,
            status="checked_in",
            check_in=reservation.check_in,
            check_out=reservation.check_out,
            agreed_rate=100,
        )
        self.db.add(stay)
        self.db.commit()
        self.user = user
        self.reservation = reservation
        self.folio = folio

    def tearDown(self):
        self.db.close()

    def test_checked_in_reservation_cannot_close_folio(self):
        with self.assertRaises(Exception) as context:
            close_folio(self.folio.id, self.db, self.user)

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(
            context.exception.detail,
            "Active stays must be checked out before the folio can be closed",
        )
        self.db.refresh(self.folio)
        self.db.refresh(self.reservation)
        self.assertEqual(self.folio.status, "open")
        self.assertEqual(self.reservation.status, "checked_in")


if __name__ == "__main__":
    unittest.main()
