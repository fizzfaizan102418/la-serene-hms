import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.models import FinancialTransaction, LedgerEntry
from app.reports import _financial_period_summary


class HistoricalBusinessDateReportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_closed_day_remains_reproducible_after_rollover(self):
        closed_day = date(2026, 9, 10)
        next_day = date(2026, 9, 11)
        with Session(self.engine) as db:
            charge = FinancialTransaction(
                transaction_no="TX-20260910-TEST1",
                business_date=closed_day,
                transaction_type="folio_charge",
                status="posted",
                description="historical room charge",
            )
            payment = FinancialTransaction(
                transaction_no="TX-20260910-TEST2",
                business_date=closed_day,
                transaction_type="folio_payment",
                status="posted",
                description="historical payment",
            )
            db.add_all([charge, payment]); db.flush()
            db.add_all([
                LedgerEntry(transaction_id=charge.id, account="Guest Receivables", direction="debit", amount=Decimal("31231.50")),
                LedgerEntry(transaction_id=charge.id, account="Revenue - room", direction="credit", amount=Decimal("25000.00")),
                LedgerEntry(transaction_id=charge.id, account="Revenue - food", direction="credit", amount=Decimal("5665.00")),
                LedgerEntry(transaction_id=charge.id, account="Revenue - service_charge", direction="credit", amount=Decimal("566.50")),
                LedgerEntry(transaction_id=payment.id, account="Cash", direction="debit", amount=Decimal("31231.50"), payment_method="cash"),
                LedgerEntry(transaction_id=payment.id, account="Guest Receivables", direction="credit", amount=Decimal("31231.50"), payment_method="cash"),
            ])
            db.commit()

            # The current operational day is now 2026-09-11, but the report for
            # 2026-09-10 must still read the immutable financial business date.
            result = _financial_period_summary(db, closed_day, next_day)
            self.assertEqual(result["net"], Decimal("31231.50"))
            self.assertEqual(result["payments_received"], Decimal("31231.50"))
            self.assertEqual(result["payments_net"], Decimal("31231.50"))
            self.assertEqual(result["outstanding_balance"], Decimal("0.00"))
            self.assertEqual(result["financial_source"], "financial_transactions.business_date + ledger_entries")

    def test_next_business_day_does_not_steal_prior_day_activity(self):
        closed_day = date(2026, 9, 10)
        next_day = date(2026, 9, 11)
        with Session(self.engine) as db:
            tx = FinancialTransaction(
                transaction_no="TX-20260910-TEST3",
                business_date=closed_day,
                transaction_type="folio_charge",
                status="posted",
                description="prior day",
                created_at=datetime(2026, 9, 11, 0, 5),
            )
            db.add(tx); db.flush()
            db.add_all([
                LedgerEntry(transaction_id=tx.id, account="Guest Receivables", direction="debit", amount=Decimal("100.00")),
                LedgerEntry(transaction_id=tx.id, account="Revenue - room", direction="credit", amount=Decimal("100.00")),
            ])
            db.commit()
            result = _financial_period_summary(db, closed_day, next_day)
            self.assertEqual(result["net"], Decimal("100.00"))


if __name__ == "__main__":
    unittest.main()
