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
from app.ledger import post_deposit_received
from app.models import BusinessDateState, DepositTransaction, FinancialTransaction, Folio, FolioItem, Guest, Reservation, ReservationRoom, Role, Room, RoomType, StayRateSegment, User
from app.pms_core import Stay
from app.room_charge_accrual import accrue_room_charges_for_business_date
from app.front_desk import post_accrued_room_charges


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

    def test_checkout_posting_completes_only_the_remaining_night_for_three_night_stay(self):
        # 2026-09-13 -> 2026-09-16 is exactly three nights.
        for business_date in (date(2026, 9, 13), date(2026, 9, 14)):
            posted = accrue_room_charges_for_business_date(
                self.db, business_date=business_date, created_by=1
            )
            self.assertEqual(posted, 1)
            self.db.commit()

        reservation = self.db.get(Reservation, 1)
        folio = self.db.get(Folio, 1)
        reservation.check_out = date(2026, 9, 16)
        stay = self.db.get(Stay, 1)
        stay.check_out = date(2026, 9, 16)
        segment = self.db.get(StayRateSegment, 1)
        segment.to_date = date(2026, 9, 16)
        self.db.commit()

        posted = post_accrued_room_charges(
            self.db, reservation, folio, date(2026, 9, 15), 1
        )
        self.assertEqual(posted, 1)
        self.db.commit()

        items = self.db.scalars(
            select(FolioItem).where(
                FolioItem.folio_id == 1,
                FolioItem.category == "room",
            )
        ).all()
        self.assertEqual(len(items), 3)
        self.assertEqual(
            sum((Decimal(item.quantity) for item in items), Decimal("0.00")),
            Decimal("3.00"),
        )

        repeated = post_accrued_room_charges(
            self.db, reservation, folio, date(2026, 9, 15), 1
        )
        self.assertEqual(repeated, 0)
        self.db.commit()

    def test_two_room_walk_in_one_night_fully_prepaid_totals_twenty_thousand(self):
        # Mirrors a walk-in: two rooms, one night, PKR 10,000 each, PKR 20,000
        # received in advance. The deposit must settle the two room charges once
        # the night is accrued; it must not create revenue or room-charge duplicates.
        room_2 = Room(id=2, number="102", room_type_id=1, status="occupied")
        self.db.add(room_2)
        reservation_room_1 = ReservationRoom(reservation_id=1, room_id=1)
        reservation_room_2 = ReservationRoom(reservation_id=1, room_id=2)
        self.db.add_all([reservation_room_1, reservation_room_2])

        stay_2 = Stay(
            id=2,
            reservation_id=1,
            room_id=2,
            guest_id=1,
            status="checked_in",
            check_in=date(2026, 9, 13),
            check_out=date(2026, 9, 14),
            agreed_rate=Decimal("10000.00"),
            payment_due_policy="at_checkout",
            deposit_required=Decimal("10000.00"),
            deposit_received=Decimal("10000.00"),
        )
        stay_1 = self.db.get(Stay, 1)
        stay_1.deposit_required = Decimal("10000.00")
        stay_1.deposit_received = Decimal("10000.00")
        segment_2 = StayRateSegment(
            id=2,
            stay_id=2,
            from_date=date(2026, 9, 13),
            to_date=date(2026, 9, 14),
            rate=Decimal("10000.00"),
            discount_amount=Decimal("0.00"),
        )
        self.db.add_all([stay_2, segment_2])
        self.db.flush()

        deposit_1 = DepositTransaction(
            id=1,
            stay_id=1,
            folio_id=1,
            transaction_type="received",
            amount=Decimal("10000.00"),
            payment_method="cash",
            reference="WALKIN-1-1",
            notes="Two-room walk-in advance",
            created_by=1,
        )
        deposit_2 = DepositTransaction(
            id=2,
            stay_id=2,
            folio_id=1,
            transaction_type="received",
            amount=Decimal("10000.00"),
            payment_method="cash",
            reference="WALKIN-1-2",
            notes="Two-room walk-in advance",
            created_by=1,
        )
        self.db.add_all([deposit_1, deposit_2])
        self.db.flush()
        post_deposit_received(
            self.db, stay_id=1, folio_id=1, reservation_id=1,
            deposit_id=1, amount=Decimal("10000.00"), method="cash", created_by=1,
        )
        post_deposit_received(
            self.db, stay_id=2, folio_id=1, reservation_id=1,
            deposit_id=2, amount=Decimal("10000.00"), method="cash", created_by=1,
        )
        self.db.commit()

        posted = accrue_room_charges_for_business_date(
            self.db, business_date=date(2026, 9, 13), created_by=1
        )
        self.assertEqual(posted, 2)
        self.db.commit()

        items = self.db.scalars(
            select(FolioItem).where(
                FolioItem.folio_id == 1,
                FolioItem.category == "room",
            )
        ).all()
        self.assertEqual(len(items), 2)
        self.assertEqual(
            sum((Decimal(item.quantity) * Decimal(item.unit_price) for item in items), Decimal("0.00")),
            Decimal("20000.00"),
        )

        summary = folio_ledger_summary(self.db, 1)
        self.assertEqual(summary.total, Decimal("20000.00"))
        self.assertEqual(summary.paid, Decimal("20000.00"))
        self.assertEqual(summary.balance, Decimal("0.00"))

        deposit_transactions = self.db.scalars(
            select(FinancialTransaction).where(
                FinancialTransaction.folio_id == 1,
                FinancialTransaction.transaction_type == "deposit_received",
                FinancialTransaction.status == "posted",
            )
        ).all()
        self.assertEqual(len(deposit_transactions), 2)
        self.assertEqual(
            sum((Decimal(entry.amount) for tx in deposit_transactions for entry in tx.entries if entry.account == "Cash" and entry.direction == "debit"), Decimal("0.00")),
            Decimal("20000.00"),
        )

        repeated = accrue_room_charges_for_business_date(
            self.db, business_date=date(2026, 9, 13), created_by=1
        )
        self.assertEqual(repeated, 0)
        self.db.commit()


if __name__ == "__main__":
    unittest.main()
