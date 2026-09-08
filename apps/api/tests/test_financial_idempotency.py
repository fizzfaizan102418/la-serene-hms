import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.ledger import post_transaction
from app.models import BusinessDateState, FinancialTransaction, LedgerEntry


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
        self.assertEqual(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.idempotency_key == "sale-1")), first.id)
        self.assertEqual(self.db.scalar(select(LedgerEntry.transaction_id).where(LedgerEntry.transaction_id == first.id)), first.id)
        self.assertEqual(self.db.query(type(first)).count(), 1)

    def test_reusing_key_for_different_transaction_payload_is_rejected(self):
        post_transaction(
            self.db,
            transaction_type="cash_sale",
            description="Room sale",
            idempotency_key="sale-2",
            reference_type="folio_item",
            reference_id="41",
            lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("25.00")},
                {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("25.00")},
            ],
        )
        with self.assertRaisesRegex(ValueError, "already bound"):
            post_transaction(
                self.db,
                transaction_type="cash_sale",
                description="Room sale",
                idempotency_key="sale-2",
                reference_type="folio_item",
                reference_id="41",
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("30.00")},
                    {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("30.00")},
                ],
            )

    def test_idempotency_key_is_trimmed_before_persistence(self):
        tx = post_transaction(
            self.db,
            transaction_type="cash_sale",
            description="Trimmed key",
            idempotency_key="  sale-3  ",
            lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("10.00")},
                {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("10.00")},
            ],
        )
        self.assertEqual(tx.idempotency_key, "sale-3")

    def test_empty_idempotency_key_does_not_create_replay_identity(self):
        first = post_transaction(
            self.db,
            transaction_type="cash_sale",
            description="Unkeyed sale",
            idempotency_key="   ",
            lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("10.00")},
                {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("10.00")},
            ],
        )
        second = post_transaction(
            self.db,
            transaction_type="cash_sale",
            description="Unkeyed sale",
            idempotency_key="   ",
            lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("10.00")},
                {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("10.00")},
            ],
        )
        self.assertNotEqual(first.id, second.id)


if __name__ == "__main__":
    unittest.main()
