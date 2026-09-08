import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.db import engine
# Register the full PMS model metadata before exercising ledger ORM mappers.
# Stay lives in pms_core.py rather than models.py and LedgerEntry references it.
from app.pms_core import Stay  # noqa: F401
from app.ledger import post_transaction
from app.models import BusinessDateState, FinancialTransaction, LedgerEntry


class PostgreSQLSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if engine.dialect.name != "postgresql":
            raise unittest.SkipTest("HMS_DATABASE_URL is not PostgreSQL")

    def test_expected_tables_exist(self):
        names = set(inspect(engine).get_table_names())
        for expected in ("stays", "stay_occupants", "stay_rate_segments", "deposit_transactions", "room_moves", "reservation_splits", "business_date_state", "financial_transactions", "ledger_entries"):
            self.assertIn(expected, names)

    def test_ledger_transaction_is_balanced_and_persistent(self):
        with Session(engine) as db:
            state = db.get(BusinessDateState, 1)
            if state is None:
                state = BusinessDateState(id=1, current_business_date=date(2026, 9, 8))
                db.add(state)
                db.flush()
            tx = post_transaction(
                db,
                transaction_type="ci_smoke",
                description="CI PostgreSQL ledger smoke test",
                created_by=None,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("10.00")},
                    {"account": "Test Revenue", "direction": "credit", "amount": Decimal("10.00")},
                ],
            )
            transaction_id = tx.id
            db.commit()

        with Session(engine) as db:
            tx = db.get(FinancialTransaction, transaction_id)
            self.assertIsNotNone(tx)
            entries = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == transaction_id)).all()
            self.assertEqual(len(entries), 2)
            self.assertEqual(sum((e.amount for e in entries if e.direction == "debit"), Decimal("0.00")), Decimal("10.00"))
            self.assertEqual(sum((e.amount for e in entries if e.direction == "credit"), Decimal("0.00")), Decimal("10.00"))


if __name__ == "__main__":
    unittest.main()
