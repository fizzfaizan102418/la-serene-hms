import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.ledger import post_transaction
from app.models import BusinessDateState


class FinancialIdempotencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime.utcnow()))
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_same_idempotency_key_returns_same_transaction(self):
        lines = [
            {"account": "Cash", "direction": "debit", "amount": Decimal("25.00")},
            {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("25.00")},
        ]
        first = post_transaction(self.db, transaction_type="cash_sale", description="Room sale", idempotency_key="sale-1", lines=lines)
        second = post_transaction(self.db, transaction_type="cash_sale", description="Room sale", idempotency_key="sale-1", lines=lines)
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.db.query(type(first)).count(), 1)

    def test_reusing_key_for_different_transaction_is_rejected(self):
        post_transaction(self.db, transaction_type="cash_sale", description="Room sale", idempotency_key="sale-2", lines=[
            {"account": "Cash", "direction": "debit", "amount": Decimal("25.00")},
            {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("25.00")},
        ])
        with self.assertRaisesRegex(ValueError, "already bound"):
            post_transaction(self.db, transaction_type="cash_sale", description="Different sale", idempotency_key="sale-2", lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("30.00")},
                {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("30.00")},
            ])


if __name__ == "__main__":
    unittest.main()
