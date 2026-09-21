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
from app.front_desk import post_accrued_room_charges
from app.financial_ops import ledger_reconciliation


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


    def test_legacy_folio_charge_uses_staff_service_charge_liability(self):
        from app.ledger import post_folio_charge

        item = FolioItem(
            id=50, folio_id=1, stay_id=1, description="Restaurant dinner",
            category="food", quantity=1, unit_price=Decimal("1000.00"),
            discount=Decimal("0.00"),
        )
        self.db.add(item)
        self.db.flush()

        post_folio_charge(
            self.db, folio_id=1, reservation_id=1, item_id=item.id,
            amount=Decimal("1000.00"), stay_id=1, category="food", created_by=1,
        )
        self.db.commit()

        tx = self.db.scalar(
            select(FinancialTransaction).where(
                FinancialTransaction.folio_id == 1,
                FinancialTransaction.transaction_type == "service_charge",
                FinancialTransaction.status == "posted",
            )
        )
        self.assertIsNotNone(tx)
        entries = self.db.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        ).all()
        self.assertEqual(
            sum((Decimal(e.amount) for e in entries
                 if e.account == "Staff Service Charges Payable" and e.direction == "credit"),
                Decimal("0.00")),
            Decimal("100.00"),
        )
        self.assertEqual(
            sum((Decimal(e.amount) for e in entries
                 if e.account == "Revenue - service_charge"),
                Decimal("0.00")),
            Decimal("0.00"),
        )

    def test_daily_closing_payments_are_net_of_payment_and_deposit_refunds(self):
        post_transaction(
            self.db, transaction_type="folio_payment", description="Test folio payment",
            reference_type="payment", reference_id="501", folio_id=1, reservation_id=1,
            created_by=1, lines=[
                {"account":"Cash","direction":"debit","amount":Decimal("100.00"),"folio_id":1,"payment_method":"cash"},
                {"account":"Guest Receivables","direction":"credit","amount":Decimal("100.00"),"folio_id":1,"payment_method":"cash"},
            ])
        post_transaction(
            self.db, transaction_type="deposit_received", description="Test guest deposit",
            reference_type="deposit", reference_id="502", folio_id=1, reservation_id=1,
            created_by=1, lines=[
                {"account":"Cash","direction":"debit","amount":Decimal("50.00"),"folio_id":1,"stay_id":1,"payment_method":"cash"},
                {"account":"Guest Deposits","direction":"credit","amount":Decimal("50.00"),"folio_id":1,"stay_id":1,"payment_method":"cash"},
            ])
        post_transaction(
            self.db, transaction_type="payment_refund", description="Test payment refund",
            reference_type="payment_refund", reference_id="503", folio_id=1, reservation_id=1,
            created_by=1, lines=[
                {"account":"Guest Receivables","direction":"debit","amount":Decimal("20.00"),"folio_id":1},
                {"account":"Cash","direction":"credit","amount":Decimal("20.00"),"folio_id":1,"payment_method":"cash"},
            ])
        post_transaction(
            self.db, transaction_type="deposit_refund", description="Test deposit refund",
            reference_type="deposit", reference_id="504", folio_id=1, reservation_id=1,
            created_by=1, lines=[
                {"account":"Guest Deposits","direction":"debit","amount":Decimal("10.00"),"folio_id":1,"stay_id":1},
                {"account":"Cash","direction":"credit","amount":Decimal("10.00"),"folio_id":1,"stay_id":1,"payment_method":"cash"},
            ])
        self.db.commit()

        summary = build_summary(self.db, date(2026, 9, 13))
        self.assertEqual(summary["payments"]["received_total"], Decimal("150.00"))
        self.assertEqual(summary["payments"]["refunded_total"], Decimal("30.00"))
        self.assertEqual(summary["payments"]["net_total"], Decimal("120.00"))
        self.assertEqual(summary["payments"]["cash"], Decimal("120.00"))
        self.assertEqual(summary["payments"]["total"], Decimal("120.00"))

        reconciliation = ledger_reconciliation(date(2026, 9, 13), self.db, self.db.get(User, 1))
        self.assertEqual(reconciliation["reconciliation"]["status"], "balanced")
        self.assertEqual(reconciliation["reconciliation"]["cash_difference"], Decimal("0.00"))



if __name__ == "__main__":
    unittest.main()
