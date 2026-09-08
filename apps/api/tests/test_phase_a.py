import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import BusinessDateState, DepositTransaction, Guest, Room, RoomType, StayOccupant, StayRateSegment, User
from app.pms_core import Stay
from app.models import Reservation


class PhaseADomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        role_guest = Guest(full_name="Booking Guest")
        occupant_guest = Guest(full_name="Occupant Guest")
        self.db.add_all([role_guest, occupant_guest])
        self.db.flush()
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add(room_type); self.db.flush()
        room = Room(number="101", room_type_id=room_type.id, status="available")
        self.db.add(room); self.db.flush()
        reservation = Reservation(guest_id=role_guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="reserved")
        self.db.add(reservation); self.db.flush()
        stay = Stay(reservation_id=reservation.id, room_id=room.id, guest_id=role_guest.id, status="reserved", check_in=reservation.check_in, check_out=reservation.check_out, agreed_rate=100, discount_percent=10, discount_amount=10, payment_due_policy="at_checkout", deposit_required=180, deposit_received=0)
        self.db.add(stay); self.db.flush()
        self.stay = stay
        self.role_guest = role_guest
        self.occupant_guest = occupant_guest

    def tearDown(self):
        self.db.rollback(); self.db.close()

    def test_multiple_occupants_and_primary(self):
        self.db.add_all([
            StayOccupant(stay_id=self.stay.id, guest_id=self.role_guest.id, role="primary", is_primary=True, check_in=self.stay.check_in, check_out=self.stay.check_out),
            StayOccupant(stay_id=self.stay.id, guest_id=self.occupant_guest.id, role="occupant", is_primary=False, check_in=self.stay.check_in, check_out=self.stay.check_out),
        ])
        self.db.commit()
        rows = self.db.query(StayOccupant).filter(StayOccupant.stay_id == self.stay.id).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(1 for row in rows if row.is_primary), 1)

    def test_rate_segments_can_model_rate_change(self):
        self.db.add_all([
            StayRateSegment(stay_id=self.stay.id, from_date=self.stay.check_in, to_date=date(2026, 9, 9), rate=100, discount_percent=10, discount_amount=10, source="reservation"),
            StayRateSegment(stay_id=self.stay.id, from_date=date(2026, 9, 9), to_date=self.stay.check_out, rate=120, discount_percent=0, discount_amount=0, source="manual"),
        ])
        self.db.commit()
        rows = self.db.query(StayRateSegment).filter(StayRateSegment.stay_id == self.stay.id).order_by(StayRateSegment.from_date).all()
        self.assertEqual([row.rate for row in rows], [100, 120])

    def test_deposit_balance_is_transaction_based(self):
        user = User(username="admin", password_hash="test", role_id=1)
        self.db.add(user); self.db.flush()
        self.db.add(DepositTransaction(stay_id=self.stay.id, transaction_type="received", amount=50, payment_method="cash", created_by=user.id))
        self.db.add(DepositTransaction(stay_id=self.stay.id, transaction_type="received", amount=30, payment_method="cash", created_by=user.id))
        self.db.add(DepositTransaction(stay_id=self.stay.id, transaction_type="refunded", amount=20, payment_method="cash", created_by=user.id))
        self.db.commit()
        balance = sum(row.amount if row.transaction_type in {"received", "adjusted"} else -row.amount for row in self.db.query(DepositTransaction).filter(DepositTransaction.stay_id == self.stay.id).all())
        self.assertEqual(balance, 60)

    def test_business_date_state_exists_as_singleton_capable_table(self):
        state = BusinessDateState(id=1, current_business_date=date(2026, 9, 8))
        self.db.add(state); self.db.commit()
        self.assertEqual(self.db.get(BusinessDateState, 1).current_business_date, date(2026, 9, 8))


if __name__ == "__main__":
    unittest.main()
