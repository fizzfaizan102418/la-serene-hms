import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.financial_authority import folio_ledger_summary
from app.models import BusinessDateState, FinancialTransaction, Folio, FolioItem, Guest, Reservation, Role, Room, RoomType, StayRateSegment, User
from app.pms_core import Stay
from app.room_charge_accrual import accrue_room_charges_for_business_date


class NightAuditRoomAccrualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)

        role = Role(id=1, name="admin")
        user = User(id=1, username="admin", password_hash="test", role_id=1)
        room_type = RoomType(id=1, name="Standard", base_rate=Decimal("10000.00"))
        room = Room(id=1, number="101", room_type_id=1, status="occupied")
        guest = Guest(id=1, full_name="Test Guest")
        reservation = Reservation(id=1, guest_id=1, check_in=date(2026, 9, 13), check_out=date(2026, 9, 15), status="checked_in")
        folio = Folio(id=1, reservation_id=1, status="open")
        stay = Stay(id=1, reservation_id=1, room_id=1, guest_id=1, status="checked_in", check_in=date(2026, 9, 13), check_out=date(2026, 9, 15), agreed_rate=Decimal("10000.00"), payment_due_policy="at_checkout")
        segment = StayRateSegment(id=1, stay_id=1, from_date=date(2026, 9, 13), to_date=date(2026, 9, 15), rate=Decimal("10000.00"), discount_amount=Decimal("0.00"))
        state = BusinessDateState(id=1, current_business_date=date(2026, 9, 13), opened_at=datetime.utcnow())
        self.db.add_all([role, user, room_type, room, guest, reservation, folio, stay, segment, state])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_posts_one_room_night_and_is_idempotent(self):
        business_date = date(2026, 9, 13)

        first = accrue_room_charges_for_business_date(self.db, business_date=business_date, created_by=1)
        self.db.commit()
        self.assertEqual(first, 1)

        items = self.db.scalars(select(FolioItem).where(FolioItem.folio_id == 1)).all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].category, "room")
        self.assertEqual(Decimal(items[0].quantity), Decimal("1.00"))
        self.assertEqual(Decimal(items[0].unit_price), Decimal("10000.00"))

        summary = folio_ledger_summary(self.db, 1)
        self.assertEqual(summary.total, Decimal("10000.00"))
        self.assertEqual(summary.balance, Decimal("10000.00"))

        second = accrue_room_charges_for_business_date(self.db, business_date=business_date, created_by=1)
        self.db.commit()
        self.assertEqual(second, 0)
        posted_transactions = self.db.scalars(
            select(FinancialTransaction).where(
                FinancialTransaction.folio_id == 1,
                FinancialTransaction.status == "posted",
            )
        ).all()
        self.assertEqual(len(posted_transactions), 1)


if __name__ == "__main__":
    unittest.main()
