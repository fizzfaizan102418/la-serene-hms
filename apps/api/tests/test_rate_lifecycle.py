import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import BusinessDateState, Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomType, StayRateSegment, User
from app.pms_core import Stay
from app.rate_lifecycle import RateAwareExtension, RateAwareRoomMove, RateOverride, business_date, extend_reservation_rate_aware, move_stay_rate_aware


class RateLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=cls.engine)

    def setUp(self):
        self.db = Session(self.engine)
        role = Role(name=f"reception-rate-{id(self)}")
        self.db.add(role)
        guest = Guest(full_name="Rate Guest")
        self.db.add(guest)
        self.db.flush()
        user = User(username=f"rate-{id(self)}", password_hash="test", role_id=role.id)
        self.db.add(user)
        room_type = RoomType(name=f"RateRoom-{guest.id}", base_rate=120)
        self.db.add(room_type)
        self.db.flush()
        room_a = Room(number=f"RA-{guest.id}", room_type_id=room_type.id, status="occupied")
        room_b = Room(number=f"RB-{guest.id}", room_type_id=room_type.id, status="available")
        self.db.add_all([room_a, room_b])
        self.db.flush()
        reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 7), check_out=date(2026, 9, 12), status="checked_in")
        self.db.add(reservation)
        self.db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=room_a.id))
        stay = Stay(reservation_id=reservation.id, room_id=room_a.id, guest_id=guest.id, status="checked_in", check_in=date(2026, 9, 7), check_out=date(2026, 9, 12), agreed_rate=Decimal("120.00"), discount_percent=Decimal("10.00"), discount_amount=Decimal("12.00"))
        self.db.add(stay)
        self.db.flush()
        self.db.add(StayRateSegment(stay_id=stay.id, from_date=date(2026, 9, 7), to_date=date(2026, 9, 12), rate=Decimal("120.00"), discount_percent=Decimal("10.00"), discount_amount=Decimal("12.00"), source="reservation"))
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8)))
        self.db.commit()
        self.db.expire_all()
        self.user = self.db.get(User, user.id)
        self.reservation = self.db.get(Reservation, reservation.id)
        self.stay = self.db.get(Stay, stay.id)
        self.room_a = self.db.get(Room, room_a.id)
        self.room_b = self.db.get(Room, room_b.id)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_extension_appends_future_rate_without_rewriting_history(self):
        result = extend_reservation_rate_aware(
            self.reservation.id,
            RateAwareExtension(new_check_out=date(2026, 9, 15), rate_overrides=[RateOverride(stay_id=self.stay.id, rate=Decimal("200.00"), discount_percent=Decimal("0"))]),
            self.db,
            self.user,
        )
        self.assertEqual(result["new_check_out"], date(2026, 9, 15))
        segments = self.db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == self.stay.id).order_by(StayRateSegment.from_date, StayRateSegment.id)).all()
        self.assertEqual([(s.from_date, s.to_date, s.rate, s.discount_amount) for s in segments], [(date(2026, 9, 7), date(2026, 9, 12), Decimal("120.00"), Decimal("12.00")), (date(2026, 9, 12), date(2026, 9, 15), Decimal("200.00"), Decimal("0.00"))])

    def test_room_move_splits_active_rate_and_preserves_history(self):
        result = move_stay_rate_aware(
            self.stay.id,
            RateAwareRoomMove(to_room_id=self.room_b.id, rate=Decimal("150.00")),
            self.db,
            self.user,
        )
        self.assertEqual(result["to_room_id"], self.room_b.id)
        segments = self.db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == self.stay.id).order_by(StayRateSegment.from_date, StayRateSegment.id)).all()
        self.assertEqual([(s.from_date, s.to_date, s.rate, s.discount_amount) for s in segments], [(date(2026, 9, 7), date(2026, 9, 8), Decimal("120.00"), Decimal("12.00")), (date(2026, 9, 8), date(2026, 9, 12), Decimal("150.00"), Decimal("0.00"))])
        self.assertEqual(self.db.get(Room, self.room_a.id).status, "dirty")
        self.assertEqual(self.db.get(Room, self.room_b.id).status, "occupied")

    def test_business_date_is_deterministic_for_rate_operations(self):
        self.assertEqual(business_date(self.db), date(2026, 9, 8))


if __name__ == "__main__":
    unittest.main()
