import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.ledger import post_transaction, reverse_transaction
from app.models import BusinessDateState, FinancialTransaction, LedgerEntry, Role, User


class DataIntegrityDestructiveTests(unittest.TestCase):
    """Regression tests for failures that must not leave partial or corrupt state."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(cls.engine, "connect")
        def _enable_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)

        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(username="integrity-admin", password_hash="test", role_id=role.id)
        self.db.add(self.user)
        self.db.add(
            BusinessDateState(
                id=1,
                current_business_date=date(2026, 9, 15),
                opened_at=datetime(2026, 9, 15, 8, 0),
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _balanced_lines(self, amount="25.00"):
        return [
            {"account": "Cash", "direction": "debit", "amount": Decimal(amount)},
            {"account": "Revenue - Test", "direction": "credit", "amount": Decimal(amount)},
        ]

    def test_unbalanced_transaction_creates_no_partial_rows(self):
        with self.assertRaisesRegex(ValueError, "Unbalanced ledger transaction"):
            post_transaction(
                self.db,
                transaction_type="destructive-test",
                description="Should not persist",
                created_by=self.user.id,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("25.00")},
                    {"account": "Revenue - Test", "direction": "credit", "amount": Decimal("24.99")},
                ],
            )

        self.assertEqual(self.db.query(FinancialTransaction).count(), 0)
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)

    def test_invalid_ledger_line_creates_no_partial_rows(self):
        with self.assertRaises(ValueError):
            post_transaction(
                self.db,
                transaction_type="destructive-test",
                description="Invalid direction",
                created_by=self.user.id,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("25.00")},
                    {"account": "Revenue - Test", "direction": "invalid", "amount": Decimal("25.00")},
                ],
            )

        self.assertEqual(self.db.query(FinancialTransaction).count(), 0)
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)

    def test_database_integrity_failure_rolls_back_transaction_and_lines(self):
        first = post_transaction(
            self.db,
            transaction_type="destructive-test",
            description="Existing transaction",
            created_by=self.user.id,
            idempotency_key="integrity-existing",
            lines=self._balanced_lines("10.00"),
        )
        self.db.commit()

        with patch("app.ledger.new_transaction_no", return_value=first.transaction_no):
            with self.assertRaises(IntegrityError):
                post_transaction(
                    self.db,
                    transaction_type="destructive-test",
                    description="Must roll back",
                    created_by=self.user.id,
                    idempotency_key="integrity-second",
                    lines=self._balanced_lines("20.00"),
                )

        self.db.rollback()
        transactions = self.db.scalars(select(FinancialTransaction).order_by(FinancialTransaction.id)).all()
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].idempotency_key, "integrity-existing")
        entries = self.db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == first.id)).all()
        self.assertEqual(len(entries), 2)
        self.assertEqual(sum(Decimal(entry.amount) for entry in entries), Decimal("20.00"))

    def test_reversal_preserves_original_ledger_and_second_reversal_creates_nothing(self):
        original = post_transaction(
            self.db,
            transaction_type="destructive-test",
            description="Original transaction",
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
        with self.assertRaisesRegex(ValueError, "already been reversed"):
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
                transaction_type="destructive-test",
                description="Closed date must reject",
                created_by=self.user.id,
                lines=self._balanced_lines("15.00"),
            )

        self.assertEqual(self.db.query(FinancialTransaction).count(), 0)
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)

    def test_orphan_ledger_entry_is_rejected_by_foreign_key(self):
        with self.assertRaises(IntegrityError):
            self.db.add(
                LedgerEntry(
                    transaction_id=999999,
                    account="Cash",
                    direction="debit",
                    amount=Decimal("10.00"),
                    currency="PKR",
                )
            )
            self.db.flush()

        self.db.rollback()
        self.assertEqual(self.db.query(LedgerEntry).count(), 0)


if __name__ == "__main__":
    unittest.main()
