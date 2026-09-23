import unittest
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.main import front_desk
from app.ledger import post_transaction
from app.models import BusinessDateState, FinancialTransaction, Folio, FolioItem, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.pms_core import Stay


class FrontDeskTransactionalIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.business_date = date(2026, 9, 8)
        self.db.add(BusinessDateState(id=1, current_business_date=self.business_date, opened_at=datetime(2026, 9, 8)))
        role = Role(name="reception")
        guest = Guest(full_name="Transactional Guest")
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add_all([role, guest, room_type]); self.db.flush()
        self.user = User(username="reception", password_hash="test", role_id=role.id)
        self.room = Room(number="101", room_type_id=room_type.id, status="available")
        self.db.add_all([self.user, self.room]); self.db.commit()
        self.guest = guest
        self.room_type = room_type

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def make_reservation(self, check_in: date, check_out: date, status: str = "reserved"):
        reservation = Reservation(guest_id=self.guest.id, check_in=check_in, check_out=check_out, status=status)
        self.db.add(reservation); self.db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=self.room.id))
        self.db.commit()
        return reservation, folio

    def make_checked_in_reservation(self, check_in: date, check_out: date, rate: Decimal = Decimal("100")):
        reservation, folio = self.make_reservation(check_in, check_out, status="checked_in")
        stay = Stay(
            reservation_id=reservation.id,
            room_id=self.room.id,
            guest_id=self.guest.id,
            status="checked_in",
            check_in=check_in,
            check_out=check_out,
            actual_check_in=datetime(2026, 9, 8, 14, 0),
            agreed_rate=rate,
            discount_percent=Decimal("0"),
            discount_amount=Decimal("0"),
        )
        self.db.add(stay)
        self.room.status = "occupied"
        self.db.commit()
        return reservation, folio, stay

    def test_front_desk_arrivals_exclude_already_checked_in_reservations(self):
        checked_in, _folio, _stay = self.make_checked_in_reservation(self.business_date, date(2026, 9, 9))
        waiting, _ = self.make_reservation(self.business_date, date(2026, 9, 10), status="reserved")

        result = front_desk(self.db, self.user)
        arrival_ids = [item.id for item in result.arrivals]
        in_house_ids = [item.id for item in result.in_house]

        self.assertNotIn(checked_in.id, arrival_ids)
        self.assertIn(waiting.id, arrival_ids)
        self.assertIn(checked_in.id, in_house_ids)



if __name__ == "__main__":
    unittest.main()
