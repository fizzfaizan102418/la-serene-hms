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

        # The refund test exercises ledger-authoritative reads, so seed the fixture
        # with the same posted charge/payment transactions used in production.
        post_transaction(
            self.db,
            transaction_type="folio_charge",
            description="Folio charge #1: room",
            reference_type="folio_item",
            reference_id=str(item_a.id),
            folio_id=folio_a.id,
            reservation_id=res_a.id,
            created_by=self.user.id,
            idempotency_key=f"regression-folio-charge:{item_a.id}",
            lines=[
                {"account": "Guest Receivables", "direction": "debit", "amount": Decimal("100.00"), "folio_id": folio_a.id},
                {"account": "Revenue - room", "direction": "credit", "amount": Decimal("100.00"), "folio_id": folio_a.id},
            ],
        )
        post_transaction(
            self.db,
            transaction_type="folio_payment",
            description=f"Payment #{payment.id}",
            reference_type="payment",
            reference_id=str(payment.id),
            folio_id=folio_a.id,
            reservation_id=res_a.id,
            created_by=self.user.id,
            idempotency_key=f"regression-folio-payment:{payment.id}",
            lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("100.00"), "folio_id": folio_a.id, "payment_method": "cash"},
                {"account": "Guest Receivables", "direction": "credit", "amount": Decimal("100.00"), "folio_id": folio_a.id, "payment_method": "cash"},
            ],
        )
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
        self.require_open = require_open_business_date
        tx = post_transaction(
            self.db,
            transaction_type="manual",
            description="Reversal test",
            reference_type="regression",
            reference_id="reverse-1",
            created_by=self.user.id,
            lines=[
                {"account": "Cash", "direction": "debit", "amount": 10},
                {"account": "Revenue - room", "direction": "credit", "amount": 10},
            ],
        )
        self.db.commit()
        reversal = reverse_transaction(self.db, tx.id, self.user.id)
        self.db.commit()
        self.assertEqual(reversal.status, "posted")
        with self.assertRaises((HTTPException, ValueError)):
            reverse_transaction(self.db, tx.id, self.user.id)

    def test_invoice_sequence_is_monotonic_without_reuse(self):
        invoice1 = Invoice(folio_id=self.folio_a.id)
        self.db.add(invoice1)
        self.db.flush()
        invoice2 = Invoice(folio_id=self.folio_b.id)
        self.db.add(invoice2)
        self.db.flush()
        self.assertLess(invoice1.invoice_number, invoice2.invoice_number)

    def test_folio_transfer_moves_item_and_creates_balanced_financial_transfer(self):
        result = transfer_folio_item(
            type("FolioTransfer", (), {"item_id": self.item_a.id, "to_folio_id": self.folio_b.id, "reason": "Regression"})(),
            self.db,
            self.user,
        )
        self.assertEqual(result["to_folio_id"], self.folio_b.id)
        tx = self.db.scalar(select(FinancialTransaction).where(FinancialTransaction.transaction_type == "folio_transfer").order_by(FinancialTransaction.id.desc()))
        self.assertIsNotNone(tx)

    def test_deposit_lifecycle_updates_balance_and_ledger(self):
        stay = self._new_stay()
        result = post_deposit(
            stay.id,
            type("DepositPayload", (), {"transaction_type": "received", "amount": Decimal("100"), "payment_method": "cash", "reference": None, "notes": None})(),
            self.db,
            self.user,
        )
        self.assertEqual(result["balance"], Decimal("100.00"))

    def test_closed_business_date_blocks_financial_posting(self):
        state = self.db.get(BusinessDateState, 1)
        state.last_closed_at = datetime(2026, 9, 8, 23, 0)
        self.db.commit()
        with self.assertRaises(HTTPException):
            post_deposit(
                self._new_stay().id,
                type("DepositPayload", (), {"transaction_type": "received", "amount": Decimal("50"), "payment_method": "cash", "reference": None, "notes": None})(),
                self.db,
                self.user,
            )
