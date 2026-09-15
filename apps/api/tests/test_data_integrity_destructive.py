from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ledger import post_transaction, reverse_transaction
from app.models import Base, BusinessDateState, FinancialTransaction, LedgerEntry, User
from app.pms_core import Stay  # noqa: F401 - register the stays table on Base.metadata


class DataIntegrityDestructiveTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(username="integrity-admin", role="admin", is_active=True)
        self.db.add(self.user)
        self.db.add(
            BusinessDateState(
                id=1,
                current_business_date=date(2026, 9, 15),
                last_closed_business_date=None,
                last_closed_at=None,
            )
        )
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _balanced_lines(amount: str = "100.00"):
        return [
            {"account": "Cash", "direction": "debit", "amount": Decimal(amount)},
            {"account": "Revenue - Room", "direction": "credit", "amount": Decimal(amount)},
        ]

    def test_unbalanced_transaction_creates_no_partial_rows(self):
        with self.assertRaisesRegex(ValueError, "Unbalanced ledger transaction"):
            post_transaction(
                self.db,
                transaction_type="integrity-test",
                description="Unbalanced",
                created_by=self.user.id,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("100.00")},
                    {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("90.00")},
                ],
            )
        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), 0)
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)

    def test_invalid_ledger_line_creates_no_partial_rows(self):
        with self.assertRaisesRegex(ValueError, "Ledger amounts must be greater than zero"):
            post_transaction(
                self.db,
                transaction_type="integrity-test",
                description="Invalid line",
                created_by=self.user.id,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("100.00")},
                    {"account": "Revenue - Room", "direction": "credit", "amount": Decimal("0.00")},
                ],
            )
        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), 0)
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)

    def test_database_integrity_failure_rolls_back_transaction_and_lines(self):
        original = post_transaction(
            self.db,
            transaction_type="integrity-test",
            description="Existing transaction",
            created_by=self.user.id,
            idempotency_key="integrity-existing",
            lines=self._balanced_lines("20.00"),
        )
        self.db.commit()
        tx_count_before = self.db.query(FinancialTransaction).count()
        entry_count_before = self.db.query(LedgerEntry).count()

        with self.assertRaisesRegex(Exception, "UNIQUE|unique|IntegrityError"):
            post_transaction(
                self.db,
                transaction_type="integrity-test",
                description="Collision",
                created_by=self.user.id,
                lines=self._balanced_lines("30.00"),
            )
            self.db.flush()

        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), tx_count_before)
        self.assertEqual(self.db.query(LedgerEntry).count(), entry_count_before)
        self.assertIsNotNone(self.db.get(FinancialTransaction, original.id))

    def test_orphan_ledger_entry_is_rejected_by_foreign_key(self):
        orphan = LedgerEntry(
            transaction_id=999999,
            account="Cash",
            direction="debit",
            amount=Decimal("10.00"),
            currency="PKR",
        )
        self.db.add(orphan)
        with self.assertRaises(Exception):
            self.db.flush()
        self.db.rollback()
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)

    def test_reversal_preserves_original_ledger_and_second_reversal_creates_nothing(self):
        original = post_transaction(
            self.db,
            transaction_type="integrity-test",
            description="Original",
            created_by=self.user.id,
            idempotency_key="integrity-reversal-original",
            lines=self._balanced_lines("40.00"),
        )
        self.db.flush()
        original_entries = [
            (entry.account, entry.direction, Decimal(entry.amount))
            for entry in self.db.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.transaction_id == original.id)
                .order_by(LedgerEntry.id)
            ).all()
        ]

        reversal = reverse_transaction(
            self.db,
            transaction_id=original.id,
            created_by=self.user.id,
            reason="Integrity test",
        )
        self.db.commit()

        self.assertEqual(original.status, "reversed")
        self.assertEqual(reversal.reversal_of_id, original.id)
        current_original_entries = [
            (entry.account, entry.direction, Decimal(entry.amount))
            for entry in self.db.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.transaction_id == original.id)
                .order_by(LedgerEntry.id)
            ).all()
        ]
        self.assertEqual(current_original_entries, original_entries)

        tx_count_before = self.db.query(FinancialTransaction).count()
        entry_count_before = self.db.query(LedgerEntry).count()
        with self.assertRaisesRegex(ValueError, "Only posted transactions can be reversed"):
            reverse_transaction(
                self.db,
                transaction_id=original.id,
                created_by=self.user.id,
                reason="Second reversal attempt",
            )
        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), tx_count_before)
        self.assertEqual(self.db.query(LedgerEntry).count(), entry_count_before)

    def test_closed_business_date_rejects_posting_without_rows(self):
        state = self.db.get(BusinessDateState, 1)
        state.last_closed_business_date = date(2026, 9, 15)
        state.last_closed_at = datetime(2026, 9, 15, 23, 59)
        self.db.commit()

        with self.assertRaisesRegex(ValueError, "closed for financial posting"):
            post_transaction(
                self.db,
                transaction_type="integrity-test",
                description="Closed date",
                created_by=self.user.id,
                lines=self._balanced_lines("50.00"),
            )
        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), 0)
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)


if __name__ == "__main__":
    unittest.main()
