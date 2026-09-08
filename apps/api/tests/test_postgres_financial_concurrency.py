import os
import threading
import unittest
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_authority  # noqa: F401
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.billing import add_payment
from app.financial_models import PaymentRefund
from app.financial_ops import RefundCreate, create_deposit_with_ledger, refund_payment
from app.ledger import post_folio_charge_authoritative, post_folio_payment
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
from app.schemas import PaymentCreate


class PostgreSQLFinancialConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.getenv("HMS_DATABASE_URL", "")
        if not url.startswith("postgresql"):
            raise unittest.SkipTest("PostgreSQL concurrency tests require HMS_DATABASE_URL")
        cls.engine = create_engine(url, pool_size=5, max_overflow=4)
        with cls.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def setUp(self):
        self.db = Session(self.engine)
        role = self.db.scalar(select(Role).where(Role.name == "admin"))
        if role is None:
            role = Role(name="admin")
            self.db.add(role)
            self.db.flush()
        self.user = User(username=f"concurrency-{uuid4().hex[:12]}", password_hash="test", role_id=role.id)
        guest = Guest(full_name=f"Concurrency Guest {uuid4().hex[:8]}")
        room_type = RoomType(name=f"Concurrency-{uuid4().hex[:8]}", base_rate=100)
        self.db.add_all([self.user, guest, room_type])
        self.db.flush()

        room = Room(number=f"C{self.db.scalar(select(func.coalesce(func.max(Room.id), 0))) + 1:04d}", room_type_id=room_type.id, status="occupied")
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
        state = self.db.get(BusinessDateState, 1)
        if state is None:
            state = BusinessDateState(id=1, current_business_date=date(2026, 9, 8))
            self.db.add(state)
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
        item = FolioItem(folio_id=folio.id, description="Concurrency room charge", category="room", quantity=1, unit_price=100, discount=0)
        self.db.add_all([stay, item])
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
        self.db.commit()
        self.folio_id = folio.id
        self.stay_id = stay.id
        self.reservation_id = reservation.id
        self.user_id = self.user.id

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _session_user(self, db):
        return db.get(User, self.user_id)

    def test_simultaneous_payments_cannot_overpay_folio(self):
        barrier = threading.Barrier(2)
        results = []

        def worker():
            db = Session(self.engine)
            try:
                user = self._session_user(db)
                barrier.wait(timeout=10)
                result = add_payment(
                    self.folio_id,
                    PaymentCreate(amount=Decimal("60.00"), method="cash"),
                    None,
                    db,
                    user,
                )
                results.append(("ok", result.id))
            except Exception as exc:  # one transaction is expected to lose the race
                results.append(("error", exc))
            finally:
                db.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for kind, _ in results if kind == "ok"), 1)
        self.assertEqual(sum(1 for kind, value in results if kind == "error" and isinstance(value, HTTPException) and value.status_code == 409), 1)

        payment_total = self.db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.folio_id == self.folio_id))
        self.assertEqual(Decimal(payment_total), Decimal("60.00"))

    def test_simultaneous_refunds_cannot_refund_same_payment_twice(self):
        payment = Payment(folio_id=self.folio_id, amount=Decimal("100.00"), method="cash")
        self.db.add(payment)
        self.db.flush()
        post_folio_payment(
            self.db,
            folio_id=self.folio_id,
            reservation_id=self.reservation_id,
            payment_id=payment.id,
            amount=Decimal("100.00"),
            method="cash",
            created_by=self.user_id,
        )
        self.db.commit()
        payment_id = payment.id

        barrier = threading.Barrier(2)
        results = []

        def worker(index):
            db = Session(self.engine)
            try:
                user = self._session_user(db)
                barrier.wait(timeout=10)
                result = refund_payment(
                    self.folio_id,
                    RefundCreate(payment_id=payment_id, amount=Decimal("60.00"), method="cash", reason=f"Race {index}"),
                    f"refund-race-{index}-{uuid4().hex[:8]}",
                    db,
                    user,
                )
                results.append(("ok", result))
            except Exception as exc:
                results.append(("error", exc))
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for kind, _ in results if kind == "ok"), 1)
        self.assertEqual(sum(1 for kind, value in results if kind == "error" and isinstance(value, HTTPException) and value.status_code == 409), 1)

        refund_total = self.db.scalar(select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(PaymentRefund.payment_id == payment_id))
        self.assertEqual(Decimal(refund_total), Decimal("60.00"))

    def test_simultaneous_deposit_applications_cannot_overspend_deposit(self):
        received = create_deposit_with_ledger(
            self.stay_id,
            {"transaction_type": "received", "amount": Decimal("100.00"), "payment_method": "cash"},
            None,
            self.db,
            self._session_user(self.db),
        )
        self.assertEqual(received["balance"], Decimal("100.00"))
        self.db.commit()

        barrier = threading.Barrier(2)
        results = []

        def worker(index):
            db = Session(self.engine)
            try:
                user = self._session_user(db)
                barrier.wait(timeout=10)
                result = create_deposit_with_ledger(
                    self.stay_id,
                    {"transaction_type": "applied", "amount": Decimal("60.00"), "reference": f"dep-race-{index}"},
                    f"deposit-race-{index}-{uuid4().hex[:8]}",
                    db,
                    user,
                )
                results.append(("ok", result))
            except Exception as exc:
                results.append(("error", exc))
            finally:
                db.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for kind, _ in results if kind == "ok"), 1)
        self.assertEqual(sum(1 for kind, value in results if kind == "error" and isinstance(value, HTTPException) and value.status_code == 409), 1)

        applied_total = self.db.scalar(select(func.coalesce(func.sum(DepositTransaction.amount), 0)).where(DepositTransaction.stay_id == self.stay_id, DepositTransaction.transaction_type == "applied"))
        self.assertEqual(Decimal(applied_total), Decimal("60.00"))
