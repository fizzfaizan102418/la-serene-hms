import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.financial_models import Invoice, InvoiceSequence, PaymentRefund
from app.financial_ops import folio_balance
from app.ledger import post_transaction, reverse_transaction
from app.models import BusinessDateState, Guest, Payment, Role, Room, RoomType, User, Folio, FolioItem, Reservation, ReservationRoom


class FinancialPhaseBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        role = Role(name="admin")
        self.db.add(role); self.db.flush()
        user = User(username="admin", password_hash="test", role_id=role.id)
        guest = Guest(full_name="Ledger Guest")
        rt = RoomType(name="Standard", base_rate=100)
        self.db.add_all([user, guest, rt]); self.db.flush()
        room = Room(number="201", room_type_id=rt.id, status="occupied")
        reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="checked_in")
        self.db.add_all([room, reservation]); self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio); self.db.flush()
        item = FolioItem(folio_id=folio.id, description="Room 201", category="room", quantity=1, unit_price=100, discount=0)
        payment = Payment(folio_id=folio.id, amount=100, method="cash")
        self.db.add_all([item, payment])
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime.utcnow()))
        self.db.commit()
        self.user = user; self.reservation = reservation; self.folio = folio; self.payment = payment

    def tearDown(self):
        self.db.rollback(); self.db.close()

    def test_reversal_creates_opposite_transaction_without_editing_entries(self):
        tx = post_transaction(self.db, transaction_type="test", description="Test", created_by=self.user.id, lines=[
            {"account": "Cash", "direction": "debit", "amount": Decimal("25.00")},
            {"account": "Revenue - Test", "direction": "credit", "amount": Decimal("25.00")},
        ])
        self.db.flush()
        reversal = reverse_transaction(self.db, transaction_id=tx.id, created_by=self.user.id, reason="Correction")
        self.db.commit()
        self.assertEqual(tx.status, "reversed")
        self.assertEqual(reversal.reversal_of_id, tx.id)

    def test_refund_record_reduces_folio_paid_balance(self):
        refund = PaymentRefund(payment_id=self.payment.id, folio_id=self.folio.id, amount=20, method="cash", reason="Guest refund", created_by=self.user.id)
        self.db.add(refund); self.db.commit()
        total, paid, balance = folio_balance(self.db, self.folio)
        self.assertEqual(total, Decimal("100.00"))
        self.assertEqual(paid, Decimal("80.00"))
        self.assertEqual(balance, Decimal("20.00"))

    def test_invoice_sequence_is_monotonic(self):
        sequence = self.db.get(InvoiceSequence, 1)
        sequence.last_number += 1
        invoice = Invoice(invoice_no=f"INV-2026-{sequence.last_number:06d}", folio_id=self.folio.id, reservation_id=self.reservation.id, business_date=date(2026, 9, 8), total=100, currency="PKR", status="issued", issued_by=self.user.id)
        self.db.add(invoice); self.db.commit()
        self.assertEqual(invoice.invoice_no, "INV-2026-000001")
        self.assertEqual(self.db.get(InvoiceSequence, 1).last_number, 1)


if __name__ == "__main__":
    unittest.main()
