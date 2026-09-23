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
from app.front_desk import create_walk_in, WalkInCreate, WalkInRoom
from app.ledger import post_transaction
from app.models import BusinessDateState, Guest, Folio, FolioItem, Payment, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.main import app


class FrontDeskPhaseCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime(2026, 9, 8, 0, 0, 0)))
        role = Role(name="reception")
        self.db.add(role); self.db.flush()
        user = User(username="reception", password_hash="test", role_id=role.id)
        guest = Guest(full_name="Walk In Guest", phone="03001234567")
        occupant = Guest(full_name="Room Occupant")
        rt = RoomType(name="Standard", base_rate=12000)
        room1 = Room(number="101", room_type_id=1, status="available")
        room2 = Room(number="102", room_type_id=1, status="available")
        self.db.add_all([user, guest, occupant, rt]); self.db.flush()
        room1.room_type_id = rt.id; room2.room_type_id = rt.id
        self.db.add_all([room1, room2]); self.db.commit()
        self.user = user; self.guest = guest; self.occupant = occupant; self.room1 = room1; self.room2 = room2

    def tearDown(self):
        self.db.rollback(); self.db.close()

    def test_walk_in_creates_checked_in_room_level_stays(self):
        result = create_walk_in(
            WalkInCreate(
                guest_id=self.guest.id,
                rooms=[WalkInRoom(room_id=self.room1.id, occupant_guest_id=self.occupant.id, agreed_rate=Decimal("12000"), discount_percent=Decimal("10"))],
                check_out=date(2026, 9, 10),
                deposit_received=Decimal("5000"),
            ),
            self.db,
            self.user,
        )
        self.assertEqual(result["status"], "checked_in")
        reservation = self.db.get(Reservation, result["reservation_id"])
        self.assertEqual(reservation.status, "checked_in")
        link = self.db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == reservation.id))
        self.assertEqual(link.room_id, self.room1.id)
        self.assertEqual(self.db.scalar(select(Folio.id).where(Folio.reservation_id == reservation.id)), result["folio_id"])
        self.assertEqual(self.db.scalar(select(Room.status).where(Room.id == self.room1.id)), "occupied")

    def test_walk_in_rejects_occupied_room(self):
        reservation = Reservation(guest_id=self.guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="checked_in")
        self.db.add(reservation); self.db.flush()
        self.room1.status = "occupied"
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=self.room1.id)); self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            create_walk_in(WalkInCreate(guest_id=self.guest.id, rooms=[WalkInRoom(room_id=self.room1.id, agreed_rate=100)], check_out=date(2026, 9, 10)), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)



if __name__ == "__main__":
    unittest.main()
