from __future__ import annotations

import os
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ledger import post_transaction, reverse_transaction
from app.models import Base, BusinessDateState, FinancialTransaction, LedgerEntry, Role, User
from app.pms_core import Stay  # noqa: F401 - register the stays table on Base.metadata


class PostgreSQLDataIntegrityDestructiveTests(unittest.TestCase):
    """Destructive integrity checks that must run against PostgreSQL, never production."""

    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ.get("HMS_TEST_DATABASE_URL", "").strip()
        if not cls.database_url:
            raise unittest.SkipTest("HMS_TEST_DATABASE_URL is not set")
        if not cls.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise unittest.SkipTest("HMS_TEST_DATABASE_URL must point to PostgreSQL")
        cls.engine = create_engine(cls.database_url, future=True, pool_pre_ping=True)
        with cls.engine.connect() as connection:
            dialect = connection.dialect.name
            if dialect != "postgresql":
                raise unittest.SkipTest("PostgreSQL engine required")
            connection.execute(text("SELECT 1"))

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "engine", None) is not None:
            cls.engine.dispose()

    def setUp(self):
        self.db = Session(self.engine)

        # Ledger rows are intentionally immutable in PostgreSQL, so ordinary
        # DELETE cannot be used to reset this disposable test database between
        # unittest methods. Disable only the two immutability triggers for the
        # fixture cleanup, then immediately restore them before the test runs.
        self.db.execute(text("ALTER TABLE ledger_entries DISABLE TRIGGER trg_ledger_entries_immutable"))
        self.db.execute(text("ALTER TABLE financial_transactions DISABLE TRIGGER trg_financial_transactions_immutable"))
        self.db.execute(text("DELETE FROM ledger_entries"))
        self.db.execute(text("DELETE FROM financial_transactions"))
        self.db.execute(text("ALTER TABLE financial_transactions ENABLE TRIGGER trg_financial_transactions_immutable"))
        self.db.execute(text("ALTER TABLE ledger_entries ENABLE TRIGGER trg_ledger_entries_immutable"))

        self.admin_role = Role(name=f"integrity-admin-{uuid4().hex[:10]}")
        self.db.add(self.admin_role)
        self.db.flush()
        self.user = User(
            username=f"integrity-admin-{uuid4().hex[:10]}",
            password_hash="not-used-by-these-tests",
            role_id=self.admin_role.id,
        )
        self.db.add(self.user)

        state = self.db.get(BusinessDateState, 1)
        if state is None:
            state = BusinessDateState(id=1)
            self.db.add(state)
        state.current_business_date = date(2026, 9, 15)
        state.last_closed_business_date = None
        state.last_closed_at = None
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

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
                transaction_type="pg-integrity-test",
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
                transaction_type="pg-integrity-test",
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

    def test_postgresql_unique_failure_rolls_back_transaction_and_lines(self):
        original = post_transaction(
            self.db,
            transaction_type="pg-integrity-test",
            description="Existing transaction",
            created_by=self.user.id,
            idempotency_key="pg-integrity-existing",
            lines=self._balanced_lines("20.00"),
        )
        self.db.commit()
        tx_count_before = self.db.query(FinancialTransaction).count()
        entry_count_before = self.db.query(LedgerEntry).count()

        with patch("app.ledger.new_transaction_no", return_value=original.transaction_no):
            with self.assertRaises(IntegrityError):
                post_transaction(
                    self.db,
                    transaction_type="pg-integrity-test",
                    description="Collision",
                    created_by=self.user.id,
                    lines=self._balanced_lines("30.00"),
                )

        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), tx_count_before)
        self.assertEqual(self.db.query(LedgerEntry).count(), entry_count_before)
        self.assertIsNotNone(self.db.get(FinancialTransaction, original.id))

    def test_postgresql_foreign_key_rejects_orphan_ledger_entry(self):
        orphan = LedgerEntry(
            transaction_id=999999999,
            account="Cash",
            direction="debit",
            amount=Decimal("10.00"),
            currency="PKR",
        )
        self.db.add(orphan)
        with self.assertRaises(IntegrityError):
            self.db.flush()
        self.db.rollback()
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)

    def test_idempotency_returns_same_transaction_without_duplicate_entries(self):
        first = post_transaction(
            self.db,
            transaction_type="pg-integrity-test",
            description="Idempotent posting",
            created_by=self.user.id,
            idempotency_key="pg-idempotency-same",
            lines=self._balanced_lines("55.00"),
        )
        self.db.commit()
        entries_after_first = self.db.query(LedgerEntry).count()

        second = post_transaction(
            self.db,
            transaction_type="pg-integrity-test",
            description="Idempotent posting",
            created_by=self.user.id,
            idempotency_key="pg-idempotency-same",
            lines=self._balanced_lines("55.00"),
        )
        self.assertEqual(second.id, first.id)
        self.assertEqual(self.db.query(FinancialTransaction).count(), 1)
        self.assertEqual(self.db.query(LedgerEntry).count(), entries_after_first)
        self.db.rollback()

    def test_conflicting_idempotency_key_is_rejected_without_new_rows(self):
        post_transaction(
            self.db,
            transaction_type="pg-integrity-test",
            description="Original payload",
            created_by=self.user.id,
            idempotency_key="pg-idempotency-conflict",
            lines=self._balanced_lines("60.00"),
        )
        self.db.commit()
        tx_count_before = self.db.query(FinancialTransaction).count()
        entry_count_before = self.db.query(LedgerEntry).count()

        with self.assertRaisesRegex(ValueError, "Idempotency key is already bound"):
            post_transaction(
                self.db,
                transaction_type="pg-integrity-test",
                description="Different payload",
                created_by=self.user.id,
                idempotency_key="pg-idempotency-conflict",
                lines=self._balanced_lines("70.00"),
            )

        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), tx_count_before)
        self.assertEqual(self.db.query(LedgerEntry).count(), entry_count_before)

    def test_reversal_preserves_original_ledger_and_rejects_second_reversal(self):
        original = post_transaction(
            self.db,
            transaction_type="pg-integrity-test",
            description="Original",
            created_by=self.user.id,
            idempotency_key="pg-reversal-original",
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
            reason="PostgreSQL integrity test",
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
                transaction_type="pg-integrity-test",
                description="Closed date",
                created_by=self.user.id,
                lines=self._balanced_lines("50.00"),
            )
        self.db.rollback()
        self.assertEqual(self.db.query(FinancialTransaction).count(), 0)
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)


if __name__ == "__main__":
    unittest.main()
