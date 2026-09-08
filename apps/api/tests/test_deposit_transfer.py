import unittest
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
import app.financial_authority  # noqa: F401
from app.deposit_transfer import DepositTransferCreate, stay_deposit_balance, transfer_deposit
from app.ledger import post_deposit_received
from app.models import BusinessDateState, DepositTransaction, FinancialTransaction, Folio, Guest, Reservation, Role, User
from app.pms_core import Stay


class DepositTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8)))
        role = Role(id=1, name="admin")
        self.db.add(role)
        self.db.add(User(id=1, username="admin", password_hash="test", role_id=1))
        guest = Guest(id=1, full_name="Test Guest")
        self.db.add(guest)
        self.db.add(Guest(id=2, full_name="Destination Guest"))
        self.db.flush()
        reservation_a = Reservation(id=1, guest_id=1, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="checked_in")
        reservation_b = Reservation(id=2, guest_id=2, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="checked_in")
        self.db.add_all([reservation_a, reservation_b])
        self.db.add_all([Folio(id=1, reservation_id=1, status="open"), Folio(id=2, reservation_id=2, status="open")])
        self.db.add_all([
            Stay(id=1, reservation_id=1, room_id=1, guest_id=1, status="checked_in", check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), agreed_rate=100, deposit_required=100),
            Stay(id=2, reservation_id=2, room_id=2, guest_id=2, status="checked_in", check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), agreed_rate=100, deposit_required=100),
        ])
        # SQLite test schemas do not enforce these FKs by default, so room IDs may be placeholders.
        self.db.flush()
        deposit = DepositTransaction(id=1, stay_id=1, folio_id=1, transaction_type="received", amount=Decimal("80.00"), payment_method="cash", reference="INITIAL-1", notes="Test deposit", created_by=1)
        self.db.add(deposit)
        self.db.flush()
        post_deposit_received(self.db, stay_id=1, folio_id=1, reservation_id=1, deposit_id=1, amount=Decimal("80.00"), method="cash", created_by=1)
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_transfer_moves_deposit_balance_atomically(self):
        result = transfer_deposit(
            1,
            DepositTransferCreate(destination_stay_id=2, amount=Decimal("30.00"), reason="Move deposit"),
            idempotency_key="deposit-transfer-1",
            db=self.db,
            user=self.db.get(User, 1),
        )

        self.assertFalse(result["replayed"])
        self.assertEqual(result["amount"], Decimal("30.00"))
        self.assertEqual(result["source_balance"], Decimal("50.00"))
        self.assertEqual(result["destination_balance"], Decimal("30.00"))
        tx = self.db.get(FinancialTransaction, result["transaction_id"])
        self.assertEqual(tx.transaction_type, "deposit_transfer")
        lines = list(tx for tx in self.db.scalars(select(FinancialTransaction).where(FinancialTransaction.id == result["transaction_id"])))
        self.assertEqual(len(lines), 1)
        entries = self.db.execute(
            select(app.models.LedgerEntry).where(app.models.LedgerEntry.transaction_id == result["transaction_id"])
        ).scalars().all()
        self.assertEqual({(e.account, e.direction, e.stay_id, e.folio_id, e.amount) for e in entries}, {
            ("Guest Deposits", "debit", 1, 1, Decimal("30.00")),
            ("Guest Deposits", "credit", 2, 2, Decimal("30.00")),
        })

    def test_same_idempotency_key_replays_without_duplicate_transfer(self):
        first = transfer_deposit(
            1,
            DepositTransferCreate(destination_stay_id=2, amount=Decimal("25.00"), reason="Retry-safe transfer"),
            idempotency_key="deposit-transfer-replay",
            db=self.db,
            user=self.db.get(User, 1),
        )
        first_tx = first["transaction_id"]
        self.db.commit()

        replay = transfer_deposit(
            1,
            DepositTransferCreate(destination_stay_id=2, amount=Decimal("25.00"), reason="Retry-safe transfer"),
            idempotency_key="deposit-transfer-replay",
            db=self.db,
            user=self.db.get(User, 1),
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["transaction_id"], first_tx)
        self.assertEqual(self.db.scalar(select(func.count(DepositTransaction.id)).where(DepositTransaction.reference == "deposit-transfer-replay")), 2)
        self.assertEqual(self.db.scalar(select(func.count(FinancialTransaction.id)).where(FinancialTransaction.idempotency_key == "deposit-transfer-replay")), 1)
        self.assertEqual(stay_deposit_balance(self.db, 1), Decimal("55.00"))
        self.assertEqual(stay_deposit_balance(self.db, 2), Decimal("25.00"))

    def test_reused_idempotency_key_with_different_parameters_is_rejected(self):
        transfer_deposit(
            1,
            DepositTransferCreate(destination_stay_id=2, amount=Decimal("20.00"), reason="Original"),
            idempotency_key="deposit-transfer-conflict",
            db=self.db,
            user=self.db.get(User, 1),
        )
        self.db.commit()
        with self.assertRaisesRegex(HTTPException, "different transfer parameters"):
            transfer_deposit(
                1,
                DepositTransferCreate(destination_stay_id=2, amount=Decimal("21.00"), reason="Changed"),
                idempotency_key="deposit-transfer-conflict",
                db=self.db,
                user=self.db.get(User, 1),
            )

    def test_transfer_cannot_exceed_source_deposit(self):
        with self.assertRaisesRegex(HTTPException, "available source deposit balance of 80.00"):
            transfer_deposit(
                1,
                DepositTransferCreate(destination_stay_id=2, amount=Decimal("80.01"), reason="Too much"),
                idempotency_key="deposit-transfer-too-large",
                db=self.db,
                user=self.db.get(User, 1),
            )

    def test_transfer_operational_records_are_immutable(self):
        transfer_deposit(
            1,
            DepositTransferCreate(destination_stay_id=2, amount=Decimal("10.00"), reason="Immutable"),
            idempotency_key="deposit-transfer-immutable",
            db=self.db,
            user=self.db.get(User, 1),
        )
        row = self.db.scalar(select(DepositTransaction).where(DepositTransaction.reference == "deposit-transfer-immutable").order_by(DepositTransaction.id))
        row.amount = Decimal("99.00")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.db.flush()


if __name__ == "__main__":
    unittest.main()
