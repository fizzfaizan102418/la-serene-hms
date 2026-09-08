import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.front_desk import atomic_checkout, create_walk_in, WalkInCreate, WalkInRoom
from app.models import Guest, Folio, FolioItem, Payment, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.main import app


class FrontDeskPhaseCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
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
            create_walk_in(WalkInCreate(guest_id=self.guest.id, rooms=[WalkInRoom(room_id=self.room1.id, agreed_rate=100)], check_out=date(2026, 9, 9)), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_atomic_checkout_requires_zero_balance(self):
        reservation = Reservation(guest_id=self.guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="checked_in")
        folio = Folio(reservation_id=1, status="open")
        self.db.add(reservation); self.db.flush(); folio.reservation_id = reservation.id
        self.db.add(folio); self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=self.room1.id))
        from app.pms_core import Stay
        self.db.add(Stay(reservation_id=reservation.id, room_id=self.room1.id, guest_id=self.guest.id, status="checked_in", check_in=reservation.check_in, check_out=reservation.check_out, agreed_rate=Decimal("100")))
        self.db.add(FolioItem(folio_id=folio.id, description="Room", category="room", quantity=1, unit_price=100, discount=0))
        self.room1.status = "occupied"; self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            atomic_checkout(reservation.id, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        payment = Payment(folio_id=folio.id, amount=100, method="cash")
        self.db.add(payment); self.db.commit()
        result = atomic_checkout(reservation.id, self.db, self.user)
        self.assertEqual(result["status"], "checked_out")
        self.assertEqual(self.db.get(Reservation, reservation.id).status, "checked_out")
        self.assertEqual(self.db.get(Folio, folio.id).status, "closed")
        self.assertEqual(self.db.get(Room, self.room1.id).status, "dirty")

    def test_atomic_checkout_route_is_mounted(self):
        paths = app.openapi().get("paths", {})
        checkout_path = "/api/reservations/{reservation_id}/checkout"
        legacy_path = "/api/reservations/{reservation_id}/check-out"
        self.assertIn(checkout_path, paths)
        self.assertIn("post", paths[checkout_path])
        self.assertIn(legacy_path, paths)
        operation_id = paths[checkout_path]["post"].get("operationId", "")
        self.assertIn("atomic_checkout", operation_id)


if __name__ == "__main__":
    unittest.main()
