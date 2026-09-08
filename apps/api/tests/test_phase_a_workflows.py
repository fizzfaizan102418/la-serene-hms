import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.main import app
from app.models import DepositTransaction, Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomMove, RoomType, StayOccupant, StayRateSegment, User
from app.phase_a_workflows import (
    DepositApply,
    DepositRequest,
    DepositTransfer,
    MoveRoomRequest,
    OccupantCreate,
    ReservationSplitRequest,
    add_stay_occupant,
    apply_deposit,
    move_checked_in_stay,
    receive_deposit,
    refund_deposit,
    split_reservation,
    transfer_deposit,
)
from app.pms_core import Stay


class PhaseAWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        role = Role(name="reception")
        self.db.add(role); self.db.flush()
        self.user = User(username="reception", password_hash="test", role_id=role.id)
        self.booking_guest = Guest(full_name="Booking Guest")
        self.occupant_a = Guest(full_name="Ahmed")
        self.occupant_b = Guest(full_name="Bilal")
        self.occupant_c = Guest(full_name="Usman")
        rt = RoomType(name="Standard", base_rate=12000)
        self.room1 = Room(number="101", room_type_id=1, status="available")
        self.room2 = Room(number="102", room_type_id=1, status="available")
        self.room3 = Room(number="103", room_type_id=1, status="available")
        self.db.add_all([self.user, self.booking_guest, self.occupant_a, self.occupant_b, self.occupant_c, rt]); self.db.flush()
        for room in (self.room1, self.room2, self.room3): room.room_type_id = rt.id
        self.db.add_all([self.room1, self.room2, self.room3]); self.db.commit()

    def tearDown(self):
        self.db.rollback(); self.db.close()

    def make_reservation(self, status="reserved", check_in=date(2026, 9, 8), check_out=date(2026, 9, 13), rooms=None):
        rooms = rooms or [self.room1]
        reservation = Reservation(guest_id=self.booking_guest.id, check_in=check_in, check_out=check_out, status=status)
        self.db.add(reservation); self.db.flush()
        self.db.add(Folio(reservation_id=reservation.id, status="open"))
        for room in rooms:
            self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))
        self.db.flush()
        return reservation

    def make_stay(self, reservation, room, guest_id=None, status=None):
        stay = Stay(
            reservation_id=reservation.id,
            room_id=room.id,
            guest_id=guest_id or reservation.guest_id,
            status=status or reservation.status,
            check_in=reservation.check_in,
            check_out=reservation.check_out,
            agreed_rate=Decimal("12000.00"),
        )
        self.db.add(stay); self.db.flush(); return stay

    def test_sharing_adds_second_occupant_without_duplicate_reservation(self):
        reservation = self.make_reservation()
        stay = self.make_stay(reservation, self.room1, guest_id=self.occupant_a.id)
        result = add_stay_occupant(stay.id, OccupantCreate(guest_id=self.occupant_b.id), self.db, self.user)
        occupants = self.db.scalars(select(StayOccupant).where(StayOccupant.stay_id == stay.id).order_by(StayOccupant.id)).all()
        self.assertEqual(result["guest_id"], self.occupant_b.id)
        self.assertEqual(len(occupants), 1)
        self.assertEqual(occupants[0].guest_id, self.occupant_b.id)
        self.assertEqual(self.db.query(Reservation).count(), 1)

    def test_deposit_lifecycle_receive_refund_apply(self):
        reservation = self.make_reservation()
        stay = self.make_stay(reservation, self.room1)
        received = receive_deposit(stay.id, DepositRequest(amount=Decimal("5000"), payment_method="cash", reference="DEP-1"), self.db, self.user)
        self.assertEqual(received["balance"], Decimal("5000.00"))
        applied = apply_deposit(stay.id, DepositApply(amount=Decimal("2000")), self.db, self.user)
        self.assertEqual(applied["balance"], Decimal("3000.00"))
        refunded = refund_deposit(stay.id, DepositRequest(amount=Decimal("1000"), payment_method="cash"), self.db, self.user)
        self.assertEqual(refunded["balance"], Decimal("2000.00"))
        with self.assertRaises(HTTPException) as ctx:
            refund_deposit(stay.id, DepositRequest(amount=Decimal("2001"), payment_method="cash"), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.db.query(DepositTransaction).count(), 3)

    def test_deposit_transfer_moves_balance_between_reservations(self):
        source_reservation = self.make_reservation(); source_stay = self.make_stay(source_reservation, self.room1)
        target_reservation = self.make_reservation(check_in=date(2026, 9, 10), check_out=date(2026, 9, 12), rooms=[self.room2]); target_stay = self.make_stay(target_reservation, self.room2)
        receive_deposit(source_stay.id, DepositRequest(amount=Decimal("4500"), payment_method="card"), self.db, self.user)
        result = transfer_deposit(source_stay.id, DepositTransfer(target_stay_id=target_stay.id, amount=Decimal("1500")), self.db, self.user)
        self.assertEqual(result["source_balance"], Decimal("3000.00"))
        self.assertEqual(result["target_balance"], Decimal("1500.00"))
        self.assertEqual(self.db.query(DepositTransaction).count(), 3)

    def test_room_move_records_history_and_preserves_single_stay(self):
        reservation = self.make_reservation(status="checked_in")
        stay = self.make_stay(reservation, self.room1, status="checked_in")
        self.room1.status = "occupied"
        result = move_checked_in_stay(stay.id, MoveRoomRequest(to_room_id=self.room2.id, reason="Guest requested quieter room"), self.db, self.user)
        self.assertEqual(result["to_room_id"], self.room2.id)
        self.assertEqual(self.db.get(Stay, stay.id).room_id, self.room2.id)
        self.assertEqual(self.db.get(Room, self.room1.id).status, "dirty")
        self.assertEqual(self.db.get(Room, self.room2.id).status, "occupied")
        self.assertEqual(self.db.query(RoomMove).count(), 1)
        self.assertEqual(self.db.query(Reservation).count(), 1)

    def test_room_move_rejects_occupied_destination(self):
        reservation = self.make_reservation(status="checked_in")
        stay = self.make_stay(reservation, self.room1, status="checked_in")
        self.room1.status = "occupied"
        other_reservation = self.make_reservation(status="checked_in", rooms=[self.room2])
        self.make_stay(other_reservation, self.room2, status="checked_in")
        self.room2.status = "occupied"
        with self.assertRaises(HTTPException) as ctx:
            move_checked_in_stay(stay.id, MoveRoomRequest(to_room_id=self.room2.id), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reservation_split_separates_room_and_clips_stay_data(self):
        reservation = self.make_reservation(check_in=date(2026, 9, 8), check_out=date(2026, 9, 15), rooms=[self.room1, self.room2])
        stay1 = self.make_stay(reservation, self.room1, guest_id=self.occupant_a.id)
        stay2 = self.make_stay(reservation, self.room2, guest_id=self.occupant_b.id)
        self.db.add_all([
            StayOccupant(stay_id=stay2.id, guest_id=self.occupant_b.id, is_primary=True, check_in=date(2026, 9, 8), check_out=date(2026, 9, 15)),
            StayRateSegment(stay_id=stay2.id, from_date=date(2026, 9, 8), to_date=date(2026, 9, 10), rate=Decimal("12000")),
            StayRateSegment(stay_id=stay2.id, from_date=date(2026, 9, 10), to_date=date(2026, 9, 15), rate=Decimal("15000")),
        ])
        self.db.commit()
        result = split_reservation(reservation.id, ReservationSplitRequest(to_date=date(2026, 9, 11), room_stay_ids=[stay2.id], reason="Corporate room split"), self.db, self.user)
        new_reservation_id = result["new_reservation_id"]
        new_stay = self.db.scalar(select(Stay).where(Stay.reservation_id == new_reservation_id))
        source_stay = self.db.get(Stay, stay2.id)
        self.assertEqual(source_stay.check_in, date(2026, 9, 8))
        self.assertEqual(source_stay.check_out, date(2026, 9, 11))
        self.assertEqual(new_stay.check_in, date(2026, 9, 11))
        self.assertEqual(new_stay.check_out, date(2026, 9, 15))
        self.assertIsNotNone(self.db.get(ReservationRoom, {"reservation_id": reservation.id, "room_id": self.room1.id}))
        self.assertIsNotNone(self.db.get(ReservationRoom, {"reservation_id": new_reservation_id, "room_id": self.room2.id}))
        source_segments = self.db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == stay2.id)).all()
        new_segments = self.db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == new_stay.id)).all()
        self.assertTrue(all(segment.to_date <= date(2026, 9, 11) for segment in source_segments))
        self.assertTrue(any(segment.from_date == date(2026, 9, 11) and segment.to_date == date(2026, 9, 15) for segment in new_segments))
        self.assertTrue(any(item.guest_id == self.occupant_b.id for item in self.db.scalars(select(StayOccupant).where(StayOccupant.stay_id == new_stay.id)).all()))

    def test_split_rejects_checked_in_reservation(self):
        reservation = self.make_reservation(status="checked_in")
        stay = self.make_stay(reservation, self.room1, status="checked_in")
        with self.assertRaises(HTTPException) as ctx:
            split_reservation(reservation.id, ReservationSplitRequest(to_date=date(2026, 9, 10), room_stay_ids=[stay.id]), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_phase_a_routes_are_mounted(self):
        paths = app.openapi().get("paths", {})
        self.assertIn("/api/stays/{stay_id}/occupants", paths)
        self.assertIn("/api/stays/{stay_id}/deposits/receive", paths)
        self.assertIn("/api/stays/{stay_id}/move-room", paths)
        self.assertIn("/api/reservations/{reservation_id}/split", paths)


if __name__ == "__main__":
    unittest.main()
