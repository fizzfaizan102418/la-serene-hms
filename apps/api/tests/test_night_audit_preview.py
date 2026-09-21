import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.models import BusinessDateState, FinancialTransaction, Folio, FolioItem, Guest, LedgerEntry, Reservation, Role, Room, RoomType, User
from app.night_audit import build_pre_close_preview, build_summary
from app.pms_core import Stay
from app.room_charge_accrual import preview_room_charges_for_business_date


class NightAuditPreviewTests(unittest.TestCase):
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
        state = BusinessDateState(id=1, current_business_date=date(2026, 9, 13), opened_at=datetime.utcnow())
        self.db.add_all([role, user, room_type, room, guest, reservation, folio, stay, state])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _add_ledger_transaction(self, tx_id, business_date, lines):
        tx = FinancialTransaction(
            id=tx_id,
            transaction_no=f"TEST-{tx_id}",
            business_date=business_date,
            transaction_type="test",
            status="posted",
            description="preview test",
            created_by=1,
        )
        self.db.add(tx)
        self.db.flush()
        for account, direction, amount in lines:
            self.db.add(LedgerEntry(transaction_id=tx.id, account=account, direction=direction, amount=Decimal(amount), currency="PKR"))
        self.db.commit()

    def test_preview_is_read_only_and_projects_pending_room_night(self):
        self._add_ledger_transaction(
            1,
            date(2026, 9, 12),
            [("Guest Receivables", "debit", "20000.00"), ("Revenue - room", "credit", "20000.00")],
        )
        self._add_ledger_transaction(
            2,
            date(2026, 9, 13),
            [("Cash", "debit", "20000.00"), ("Guest Receivables", "credit", "20000.00")],
        )

        before_items = self.db.scalar(select(FolioItem.id))
        summary = build_summary(self.db, date(2026, 9, 13))
        preview = build_pre_close_preview(self.db, date(2026, 9, 13), summary)

        self.assertEqual(preview["opening"]["cash"], Decimal("0.00"))
        self.assertEqual(preview["opening"]["guest_receivables"], Decimal("20000.00"))
        self.assertEqual(preview["activity"]["cash_received"], Decimal("20000.00"))
        self.assertEqual(preview["activity"]["guest_receivables_delta"], Decimal("-20000.00"))
        self.assertEqual(preview["pending_night_audit"]["room_charges_count"], 1)
        self.assertEqual(preview["pending_night_audit"]["room_charges_total"], Decimal("10000.00"))
        self.assertEqual(preview["projected_close"]["cash"], Decimal("20000.00"))
        self.assertEqual(preview["projected_close"]["guest_receivables"], Decimal("10000.00"))
        self.assertEqual(preview["projected_close"]["room_revenue"], Decimal("10000.00"))

        after_items = self.db.scalar(select(FolioItem.id))
        self.assertEqual(before_items, after_items)
        self.assertEqual(len(preview_room_charges_for_business_date(self.db, business_date=date(2026, 9, 13))), 1)
        self.assertEqual(self.db.scalar(select(FolioItem.id)), None)

    def test_summary_includes_guest_deposit_in_cashier_collections(self):
        tx = FinancialTransaction(
            id=3,
            transaction_no="TEST-3",
            business_date=date(2026, 9, 13),
            transaction_type="deposit_received",
            status="posted",
            description="guest deposit",
            created_by=1,
        )
        self.db.add(tx)
        self.db.flush()
        self.db.add_all([
            LedgerEntry(transaction_id=3, account="Cash", direction="debit", amount=Decimal("25000.00"), currency="PKR", payment_method="cash"),
            LedgerEntry(transaction_id=3, account="Guest Deposits", direction="credit", amount=Decimal("25000.00"), currency="PKR", payment_method="cash"),
        ])
        self.db.commit()

        summary = build_summary(self.db, date(2026, 9, 13))

        self.assertEqual(summary["revenue"]["gross"], Decimal("0.00"))
        self.assertEqual(summary["payments"]["cash"], Decimal("25000.00"))
        self.assertEqual(summary["payments"]["total"], Decimal("25000.00"))

    def test_preview_does_not_include_checkout_date_as_room_night(self):
        stay = self.db.get(Stay, 1)
        stay.check_out = date(2026, 9, 13)
        self.db.commit()
        summary = build_summary(self.db, date(2026, 9, 13))
        preview = build_pre_close_preview(self.db, date(2026, 9, 13), summary)
        self.assertEqual(preview["pending_night_audit"]["room_charges_count"], 0)
        self.assertEqual(preview["pending_night_audit"]["room_charges_total"], Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
