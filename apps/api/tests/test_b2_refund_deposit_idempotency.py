import unittest
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_authority  # noqa: F401
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.financial_authority import post_folio_charge_authoritative
from app.financial_models import PaymentRefund
from app.financial_ops import RefundCreate, create_deposit_with_ledger, refund_payment
from app.ledger import post_folio_payment
from app.models import (
    BusinessDateState,
    DepositTransaction,
    FinancialTransaction,
    Folio,
    FolioItem,
    Guest,
    Payment,
    Reservation,
    ReservationRoom,
    Role,
    Room,
    RoomType,
    User,
)
from app.pms_core import Stay


class PhaseB2RefundDepositIdempotencyTests(unittest.TestCase):
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
        guest = Guest(full_name="Refund Guest")
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add_all([self.user, guest, room_type])
        self.db.flush()

        room = Room(number="301", room_type_id=room_type.id, status="occupied")
        reservation = Reservation(
            guest_id=guest.id,
            check_in=date(2026, 9, 8),
            check_out=date(2026, 9, 10),
            status="checked_in",
        )
        self.db.add_all([room, reservation])
        self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))

        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime(2026, 9, 8, 8, 0)))
        self.db.flush()

        stay = Stay(
            reservation_id=reservation.id,
            room_id=room.id,
            status="in_house",
            check_in=reservation.check_in,
            check_out=reservation.check_out,
            deposit_required=200,
            deposit_received=0,
        )
        item = FolioItem(folio_id=folio.id, description="Room charge", category="room", quantity=1, unit_price=100, discount=0)
        payment = Payment(folio_id=folio.id, amount=100, method="cash")
        self.db.add_all([stay, item, payment])
        self.db.flush()
        post_folio_charge_authoritative(
            self.db,
            folio_id=folio.id,
            reservation_id=reservation.id,
            item_id=item.id,
            amount=Decimal("100.00"),
            stay_id=stay.id,
            category="room",
            created_by=self.user.id,
            gross_amount=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
        )
        post_folio_payment(
            self.db,
            folio_id=folio.id,
            reservation_id=reservation.id,
            payment_id=payment.id,
            amount=Decimal("100.00"),
            method="cash",
            created_by=self.user.id,
        )
        self.db.commit()
        self.db.refresh(folio)
        self.db.refresh(payment)
        self.db.refresh(stay)
        self.folio = folio
        self.payment = payment
        self.stay = stay
        self.reservation = reservation

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_refund_is_idempotent_and_same_key_rejects_different_amount(self):
        payload = RefundCreate(
            payment_id=self.payment.id,
            amount=Decimal("40.00"),
            method="cash",
            reference="R-1",
            reason="Guest request",
        )
        first = refund_payment(self.folio.id, payload, "refund-001", self.db, self.user)
        replay = refund_payment(self.folio.id, payload, "refund-001", self.db, self.user)

        self.assertEqual(first["id"], replay["id"])
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(PaymentRefund).where(PaymentRefund.payment_id == self.payment.id)),
            1,
        )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(FinancialTransaction).where(FinancialTransaction.idempotency_key == "refund-001")),
            1,
        )

        conflicting = payload.model_copy(update={"amount": Decimal("35.00")})
        with self.assertRaisesRegex(HTTPException, "different refund parameters"):
            refund_payment(self.folio.id, conflicting, "refund-001", self.db, self.user)

    def test_deposit_application_is_idempotent_and_uses_ledger_balance(self):
        received = create_deposit_with_ledger(
            self.stay.id,
            {"transaction_type": "received", "amount": Decimal("100.00"), "payment_method": "cash"},
            None,
            self.db,
            self.user,
        )
        self.assertEqual(received["balance"], Decimal("100.00"))

        self.stay.deposit_received = Decimal("999.00")
        applied = create_deposit_with_ledger(
            self.stay.id,
            {"transaction_type": "applied", "amount": Decimal("40.00"), "reference": "APP-1"},
            "deposit-apply-001",
            self.db,
            self.user,
        )
        replay = create_deposit_with_ledger(
            self.stay.id,
            {"transaction_type": "applied", "amount": Decimal("40.00"), "reference": "APP-1"},
            "deposit-apply-001",
            self.db,
            self.user,
        )

        self.assertEqual(applied["balance"], Decimal("60.00"))
        self.assertEqual(replay["balance"], Decimal("60.00"))
        self.assertFalse(applied["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            self.db.scalar(
                select(func.count()).select_from(FinancialTransaction).where(FinancialTransaction.transaction_type == "deposit_applied")
            ),
            1,
        )
        self.assertEqual(
            self.db.scalar(
                select(func.count()).select_from(DepositTransaction).where(DepositTransaction.reference == "deposit-apply-001")
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
