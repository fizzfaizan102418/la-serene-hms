import unittest
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.business_date import get_current_business_date
from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.finance_controls import require_open_business_date
from app.ledger import post_transaction
from app.models import BusinessDateState


class FinancialBusinessDateHardeningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_finance_controls_rejects_missing_business_date(self):
        with Session(self.engine) as db:
            with self.assertRaises(HTTPException) as context:
                require_open_business_date(db)
            self.assertEqual(context.exception.status_code, 503)

    def test_financial_posting_uses_persisted_business_date(self):
        business_date = date(2026, 9, 10)
        with Session(self.engine) as db:
            db.add(BusinessDateState(id=1, current_business_date=business_date, opened_at=datetime(2026, 9, 10, 5, 0, 0)))
            db.commit()
            tx = post_transaction(
                db,
                transaction_type="test",
                description="business-date hardening",
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("100.00")},
                    {"account": "Revenue - test", "direction": "credit", "amount": Decimal("100.00")},
                ],
                idempotency_key="business-date-hardening-1",
            )
            self.assertEqual(tx.business_date, business_date)

    def test_financial_posting_rejects_closed_business_date(self):
        business_date = date(2026, 9, 10)
        with Session(self.engine) as db:
            db.add(
                BusinessDateState(
                    id=1,
                    current_business_date=business_date,
                    opened_at=datetime(2026, 9, 10, 5, 0, 0),
                    last_closed_at=datetime(2026, 9, 10, 23, 59, 0),
                )
            )
            db.commit()
            with self.assertRaises(ValueError) as context:
                post_transaction(
                    db,
                    transaction_type="test",
                    description="closed-day posting",
                    lines=[
                        {"account": "Cash", "direction": "debit", "amount": Decimal("100.00")},
                        {"account": "Revenue - test", "direction": "credit", "amount": Decimal("100.00")},
                    ],
                )
            self.assertIn("closed for financial posting", str(context.exception))

    def test_authoritative_business_date_remains_strict(self):
        with Session(self.engine) as db:
            with self.assertRaises(HTTPException) as context:
                get_current_business_date(db)
            self.assertEqual(context.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
