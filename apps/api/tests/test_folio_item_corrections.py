import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.financial_authority import folio_ledger_summary, post_folio_charge_authoritative
from app.folio_corrections import ITEM_TRANSACTION_REFERENCES, FolioItemCorrection, correct_folio_item, reverse_folio_item
from app.models import BusinessDateState, FinancialTransaction, Folio, FolioItem, Guest, LedgerEntry, Reservation, User


class FolioItemCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 11), opened_at=datetime.utcnow()))
        self.db.add(User(id=1, username="admin", password_hash="test", role_id=1))
        self.db.add(Guest(id=1, full_name="Test Guest"))
        self.db.add(Reservation(id=1, guest_id=1, check_in=date(2026, 9, 11), check_out=date(2026, 9, 12), status="checked_in"))
        self.db.add(Folio(id=1, reservation_id=1, status="open"))
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _food_item(self):
        item = FolioItem(
            folio_id=1,
            description="Dinner",
            category="food",
            quantity=1,
            unit_price=Decimal("100.00"),
            discount=Decimal("10.00"),
        )
        self.db.add(item)
        self.db.flush()
        post_folio_charge_authoritative(
            self.db,
            folio_id=1,
            reservation_id=1,
            item_id=item.id,
            amount=Decimal("90.00"),
            stay_id=None,
            category="food",
            created_by=1,
            gross_amount=Decimal("100.00"),
            discount_amount=Decimal("10.00"),
        )
        self.db.commit()
        return item

    def _item_transactions(self, item_id):
        return self.db.scalars(
            select(FinancialTransaction).where(
                FinancialTransaction.reference_id == str(item_id),
                FinancialTransaction.reference_type.in_(ITEM_TRANSACTION_REFERENCES),
            )
        ).all()

    def _guest_receivable_net(self, transaction_ids):
        entries = self.db.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id.in_(transaction_ids), LedgerEntry.account == "Guest Receivables")
        ).all()
        return sum((entry.amount if entry.direction == "debit" else -entry.amount for entry in entries), Decimal("0.00"))

    def test_reverse_reverses_charge_discount_and_food_service_charge_atomically(self):
        item = self._food_item()
        result = reverse_folio_item(1, item.id, "Removed by manager", self.db, self.db.get(User, 1))
        self.assertEqual(result.action, "reversed")
        self.assertEqual(len(result.reversed_transaction_ids), 3)
        statuses = [tx.status for tx in self._item_transactions(item.id)]
        self.assertEqual(statuses, ["reversed", "reversed", "reversed"])
        summary = folio_ledger_summary(self.db, 1)
        self.assertEqual(summary.balance, Decimal("0.00"))

    def test_correct_reverses_old_financials_and_posts_replacement_in_one_operation(self):
        item = self._food_item()
        result = correct_folio_item(
            1,
            item.id,
            FolioItemCorrection(
                description="Dinner corrected",
                category="food",
                quantity=1,
                unit_price=Decimal("80.00"),
                discount=Decimal("5.00"),
                reason="Manager corrected guest bill",
            ),
            self.db,
            self.db.get(User, 1),
        )
        self.assertEqual(result.action, "corrected")
        self.assertIsNotNone(result.replacement_item)
        self.assertNotEqual(result.replacement_item.id, item.id)
        self.assertEqual(result.replacement_item.line_total, Decimal("75.00"))
        original_statuses = [tx.status for tx in self._item_transactions(item.id)]
        self.assertEqual(original_statuses, ["reversed", "reversed", "reversed"])
        replacement_transactions = self._item_transactions(result.replacement_item.id)
        self.assertEqual(len(replacement_transactions), 3)
        self.assertTrue(all(tx.status == "posted" for tx in replacement_transactions))
        self.assertEqual(self._guest_receivable_net([tx.id for tx in replacement_transactions]), Decimal("82.50"))


if __name__ == "__main__":
    unittest.main()
