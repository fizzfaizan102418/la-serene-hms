import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Guest, Role, Room, RoomType, StayRateSegment, User
from app.pms_core import Stay
from app.models import Reservation
from app.pms_domain import RateSegmentCreate, replace_rate_segment


class RateSegmentReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)

        role = Role(name="admin")
        guest = Guest(full_name="Rate Test Guest")
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add_all([role, guest, room_type])
        self.db.flush()

        room = Room(number="101", room_type_id=room_type.id, status="available")
        reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 13), status="reserved")
        self.db.add_all([room, reservation])
        self.db.flush()

        self.stay = Stay(
            reservation_id=reservation.id,
            room_id=room.id,
            guest_id=guest.id,
            status="reserved",
            check_in=reservation.check_in,
            check_out=reservation.check_out,
            agreed_rate=100,
            discount_percent=0,
            discount_amount=0,
            payment_due_policy="at_checkout",
            deposit_required=0,
            deposit_received=0,
        )
        user = User(username="admin", password_hash="test", role_id=role.id)
        self.db.add_all([self.stay, user])
        self.db.flush()
        self.user = user

        self.db.add(
            StayRateSegment(
                stay_id=self.stay.id,
                from_date=date(2026, 9, 8),
                to_date=date(2026, 9, 13),
                rate=100,
                discount_percent=0,
                discount_amount=0,
                rate_plan="BAR",
                source="reservation",
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_middle_range_splits_existing_segment(self):
        payload = RateSegmentCreate(
            from_date=date(2026, 9, 10),
            to_date=date(2026, 9, 12),
            rate=150,
            discount_percent=10,
            source="manual",
        )
        replace_rate_segment(self.db, self.stay, payload, self.user.id)
        self.db.commit()

        rows = self.db.query(StayRateSegment).filter(StayRateSegment.stay_id == self.stay.id).order_by(StayRateSegment.from_date).all()
        self.assertEqual([(r.from_date, r.to_date, r.rate, r.discount_percent) for r in rows], [
            (date(2026, 9, 8), date(2026, 9, 10), 100, 0),
            (date(2026, 9, 10), date(2026, 9, 12), 150, 10),
            (date(2026, 9, 12), date(2026, 9, 13), 100, 0),
        ])

    def test_full_range_replacement_has_one_segment(self):
        payload = RateSegmentCreate(
            from_date=date(2026, 9, 8),
            to_date=date(2026, 9, 13),
            rate=125,
            discount_amount=5,
            source="manual",
        )
        replace_rate_segment(self.db, self.stay, payload, self.user.id)
        self.db.commit()

        rows = self.db.query(StayRateSegment).filter(StayRateSegment.stay_id == self.stay.id).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rate, 125)
        self.assertEqual(rows[0].discount_amount, 5)

    def test_adjacent_range_does_not_overlap(self):
        payload = RateSegmentCreate(
            from_date=date(2026, 9, 13),
            to_date=date(2026, 9, 13),
            rate=140,
            source="manual",
        )
        with self.assertRaises(Exception):
            replace_rate_segment(self.db, self.stay, payload, self.user.id)


if __name__ == "__main__":
    unittest.main()
