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
from app.models import BusinessDateState, FinancialTransaction, Folio, FolioItem, Invoice, LedgerEntry


class FinancialAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime.utcnow()))
        self.db.add(Folio(id=1, reservation_id=1, status="open"))
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def post(self, tx_type, lines, reference_id=None):
        tx = FinancialTransaction(transaction_no=f"TEST-{tx_type}-{reference_id or 'x'}", business_date=date(2026, 9, 8), transaction_type=tx_type, status="posted", reference_type="folio_item" if reference_id else None, reference_id=str(reference_id) if reference_id else None, folio_id=1, description=tx_type)
        self.db.add(tx)
        self.db.flush()
        for line in lines:
            self.db.add(LedgerEntry(transaction_id=tx.id, account=line[0], direction=line[1], amount=line[2], folio_id=1))
        self.db.flush()
        return tx

    def test_summary_comes_from_receivable_ledger(self):
        self.post("folio_charge", [("Guest Receivables", "debit", Decimal("100.00")), ("Revenue - Room", "credit", Decimal("100.00"))], reference_id=1)
        self.post("folio_discount", [("Revenue - Room", "debit", Decimal("10.00")), ("Guest Receivables", "credit", Decimal("10.00"))], reference_id=1)
        self.post("folio_payment", [("Cash", "debit", Decimal("90.00")), ("Guest Receivables", "credit", Decimal("90.00"))], reference_id=1)
        summary = folio_ledger_summary(self.db, 1)
        self.assertEqual(summary.total, Decimal("90.00"))
        self.assertEqual(summary.paid, Decimal("90.00"))
        self.assertEqual(summary.balance, Decimal("0.00"))

    def test_refund_reopens_receivable_without_changing_revenue_total(self):
        self.post("folio_charge", [("Guest Receivables", "debit", Decimal("100.00")), ("Revenue - Room", "credit", Decimal("100.00"))], reference_id=1)
        self.post("folio_payment", [("Cash", "debit", Decimal("100.00")), ("Guest Receivables", "credit", Decimal("100.00"))], reference_id=1)
        self.post("payment_refund", [("Guest Receivables", "debit", Decimal("20.00")), ("Cash", "credit", Decimal("20.00"))], reference_id=1)
        summary = folio_ledger_summary(self.db, 1)
        self.assertEqual(summary.total, Decimal("100.00"))
        self.assertEqual(summary.paid, Decimal("80.00"))
        self.assertEqual(summary.balance, Decimal("20.00"))

    def test_invoice_total_is_derived_from_ledger(self):
        self.post("folio_charge", [("Guest Receivables", "debit", Decimal("75.00")), ("Revenue - Room", "credit", Decimal("75.00"))], reference_id=1)
        invoice = Invoice(invoice_no="INV-TEST-1", folio_id=1, reservation_id=1, business_date=date(2026, 9, 8), total=Decimal("999.00"), currency="PKR", status="issued", issued_by=1)
        self.db.add(invoice)
        self.db.flush()
        self.assertEqual(invoice.total, Decimal("75.00"))

    def test_posted_folio_item_cannot_be_mutated(self):
        item = FolioItem(id=1, folio_id=1, description="Room", category="room", quantity=1, unit_price=Decimal("100.00"), discount=0)
        self.db.add(item)
        self.db.flush()
        self.post("folio_charge", [("Guest Receivables", "debit", Decimal("100.00")), ("Revenue - Room", "credit", Decimal("100.00"))], reference_id=item.id)
        item.unit_price = Decimal("50.00")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.db.flush()
        self.db.rollback()


if __name__ == "__main__":
    unittest.main()
