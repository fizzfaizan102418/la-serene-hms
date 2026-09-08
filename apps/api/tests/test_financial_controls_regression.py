import unittest
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.finance_controls import post_deposit, require_open_business_date, transfer_folio_item
from app.financial_ops import folio_balance, refund_payment
from app.financial_models import Invoice, InvoiceSequence, PaymentRefund
from app.ledger import post_transaction, reverse_transaction
from app.models import (
    BusinessDateState,
    FinancialTransaction,
    Folio,
    FolioItem,
    Guest,
    LedgerEntry,
    Payment,
    Reservation,
    ReservationRoom,
    Role,
    Room,
    RoomType,
    User,
)
from app.pms_core import Stay


class FinancialControlsRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)

        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(username="admin", password_hash="test", role_id=role.id)
        guest_a = Guest(full_name="Primary Guest")
        guest_b = Guest(full_name="Second Guest")
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add_all([self.user, guest_a, guest_b, room_type])
        self.db.flush()

        room_a = Room(number="201", room_type_id=room_type.id, status="occupied")
        room_b = Room(number="202", room_type_id=room_type.id, status="occupied")
        res_a = Reservation(
            guest_id=guest_a.id,
            check_in=date(2026, 9, 8),
            check_out=date(2026, 9, 10),
            status="checked_in",
        )
        res_b = Reservation(
            guest_id=guest_b.id,
            check_in=date(2026, 9, 8),
            check_out=date(2026, 9, 10),
            status="checked_in",
        )
        self.db.add_all([room_a, room_b, res_a, res_b])
        self.db.flush()
        self.db.add_all([
            ReservationRoom(reservation_id=res_a.id, room_id=room_a.id),
            ReservationRoom(reservation_id=res_b.id, room_id=room_b.id),
        ])

        folio_a = Folio(reservation_id=res_a.id, status="open")
        folio_b = Folio(reservation_id=res_b.id, status="open")
        self.db.add_all([folio_a, folio_b])
        self.db.flush()

        item_a = FolioItem(
            folio_id=folio_a.id,
            description="Room charge",
            category="room",
            quantity=1,
            unit_price=100,
            discount=0,
        )
        item_b = FolioItem(
            folio_id=folio_b.id,
            description="Room charge",
            category="room",
            quantity=1,
            unit_price=50,
            discount=0,
        )
        payment = Payment(folio_id=folio_a.id, amount=100, method="cash")
        self.db.add_all([item_a, item_b, payment])
        self.db.add(
            BusinessDateState(
                id=1,
                current_business_date=date(2026, 9, 8),
                opened_at=datetime(2026, 9, 8, 8, 0),
            )
        )
        self.db.add(InvoiceSequence(id=1, last_number=0))
        self.db.commit()

        self.res_a = res_a
        self.res_b = res_b
        self.folio_a = folio_a
        self.folio_b = folio_b
        self.payment = payment
        self.item_a = item_a
        self.room_a = room_a

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _new_stay(self, deposit_required=200):
        stay = Stay(
            reservation_id=self.res_a.id,
            room_id=self.room_a.id,
            status="in_house",
            check_in=self.res_a.check_in,
            check_out=self.res_a.check_out,
            deposit_required=deposit_required,
            deposit_received=0,
        )
        self.db.add(stay)
        self.db.commit()
        self.db.refresh(stay)
        return stay

    def test_full_and_partial_refund_limits_are_enforced(self):
        first_payload = type(
            "RefundPayload",
            (),
            {
                "payment_id": self.payment.id,
                "amount": Decimal("30.00"),
                "method": "cash",
                "reference": None,
                "reason": "Partial refund",
            },
        )()
        first = refund_payment(self.folio_a.id, first_payload, "regression-refund-1", self.db, self.user)

        total, paid, balance = folio_balance(self.db, self.folio_a)
        self.assertEqual(total, Decimal("100.00"))
        self.assertEqual(paid, Decimal("70.00"))
        self.assertEqual(balance, Decimal("30.00"))

        over_refund = Decimal(self.payment.amount) - Decimal(first["amount"]) + Decimal("0.01")
        payload = type(
            "RefundPayload",
            (),
            {
                "payment_id": self.payment.id,
                "amount": over_refund,
                "method": "cash",
                "reference": None,
                "reason": "Too much",
            },
        )()
        with self.assertRaises(HTTPException):
            refund_payment(self.folio_a.id, payload, "regression-refund-2", self.db, self.user)

    def test_reversal_is_immutable_and_cannot_be_reversed_twice(self):
        tx = post_transaction(
            self.db,
            transaction_type="test",
            description="Test revenue",
            created_by=self.user.id,
            lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("25.00")},
                {"account": "Revenue - Test", "direction": "credit", "amount": Decimal("25.00")},
            ],
        )
        self.db.flush()
        before = [
            (entry.account, entry.direction, entry.amount)
            for entry in self.db.scalars(
                select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
            ).all()
        ]

        reversal = reverse_transaction(
            self.db,
            transaction_id=tx.id,
            created_by=self.user.id,
            reason="Correction",
        )
        self.db.commit()

        after = [
            (entry.account, entry.direction, entry.amount)
            for entry in self.db.scalars(
                select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
            ).all()
        ]
        self.assertEqual(before, after)
        self.assertEqual(tx.status, "reversed")
        self.assertEqual(reversal.reversal_of_id, tx.id)
        with self.assertRaises(ValueError):
            reverse_transaction(
                self.db,
                transaction_id=tx.id,
                created_by=self.user.id,
                reason="Duplicate reversal",
            )

    def test_folio_transfer_moves_item_and_creates_balanced_financial_transfer(self):
        payload = type(
            "TransferPayload",
            (),
            {"item_id": self.item_a.id, "to_folio_id": self.folio_b.id, "reason": "Group routing"},
        )()
        result = transfer_folio_item(payload, self.db, self.user)

        self.assertEqual(result["from_folio_id"], self.folio_a.id)
        self.assertEqual(result["to_folio_id"], self.folio_b.id)
        self.db.expire_all()
        moved = self.db.get(FolioItem, self.item_a.id)
        self.assertEqual(moved.folio_id, self.folio_b.id)

        tx = self.db.scalar(
            select(FinancialTransaction).where(
                FinancialTransaction.transaction_type == "folio_transfer"
            )
        )
        self.assertIsNotNone(tx)
        entries = self.db.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)
        ).all()
        self.assertEqual(
            sum(entry.amount for entry in entries if entry.direction == "debit"),
            sum(entry.amount for entry in entries if entry.direction == "credit"),
        )

    def test_invoice_sequence_is_monotonic_without_reuse(self):
        sequence = self.db.get(InvoiceSequence, 1)
        sequence.last_number += 1
        invoice_a = Invoice(
            invoice_no="INV-2026-000001",
            folio_id=self.folio_a.id,
            reservation_id=self.res_a.id,
            business_date=date(2026, 9, 8),
            total=100,
            currency="PKR",
            status="issued",
            issued_by=self.user.id,
        )
        self.db.add(invoice_a)
        self.db.commit()
        self.assertEqual(self.db.get(InvoiceSequence, 1).last_number, 1)

        sequence = self.db.get(InvoiceSequence, 1)
        sequence.last_number += 1
        invoice_b = Invoice(
            invoice_no="INV-2026-000002",
            folio_id=self.folio_b.id,
            reservation_id=self.res_b.id,
            business_date=date(2026, 9, 8),
            total=50,
            currency="PKR",
            status="issued",
            issued_by=self.user.id,
        )
        self.db.add(invoice_b)
        self.db.commit()
        self.assertEqual(invoice_b.invoice_no, "INV-2026-000002")
        self.assertEqual(self.db.get(InvoiceSequence, 1).last_number, 2)

    def test_closed_business_date_blocks_financial_posting(self):
        state = self.db.get(BusinessDateState, 1)
        state.last_closed_at = datetime(2026, 9, 8, 23, 59)
        self.db.commit()

        with self.assertRaises(HTTPException):
            require_open_business_date(self.db)

        with self.assertRaises(ValueError):
            post_transaction(
                self.db,
                transaction_type="blocked",
                description="Should not post",
                business_date=date(2026, 9, 8),
                created_by=self.user.id,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("10.00")},
                    {"account": "Revenue - Test", "direction": "credit", "amount": Decimal("10.00")},
                ],
            )

    def test_deposit_lifecycle_updates_balance_and_ledger(self):
        stay = self._new_stay()
        received_payload = type(
            "DepositPayload",
            (),
            {
                "transaction_type": "received",
                "amount": Decimal("100.00"),
                "payment_method": "cash",
                "reference": "DEP-1",
                "notes": "Initial",
            },
        )()
        received = post_deposit(stay.id, received_payload, self.db, self.user)
        self.assertEqual(received["balance"], Decimal("100.00"))

        applied_payload = type(
            "DepositPayload",
            (),
            {
                "transaction_type": "applied",
                "amount": Decimal("40.00"),
                "payment_method": None,
                "reference": "APP-1",
                "notes": "Applied",
            },
        )()
        applied = post_deposit(stay.id, applied_payload, self.db, self.user)
        self.assertEqual(applied["balance"], Decimal("60.00"))

        tx_types = self.db.scalars(
            select(FinancialTransaction.transaction_type).order_by(FinancialTransaction.id)
        ).all()
        self.assertIn("deposit_received", tx_types)
        self.assertIn("deposit_applied", tx_types)


if __name__ == "__main__":
    unittest.main()
