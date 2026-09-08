import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.financial_authority  # noqa: F401
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
from app.db import engine
from app.financial_authority import folio_ledger_summary
from app.financial_models import PaymentRefund
from app.ledger import post_transaction
from app.models import BusinessDateState, DepositTransaction, FinancialTransaction, Folio, Guest, LedgerEntry, Payment, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.pms_core import Stay  # noqa: F401


class PostgreSQLB2ConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if engine.dialect.name != "postgresql":
            raise unittest.SkipTest("HMS_DATABASE_URL is not PostgreSQL")

    def _fixture(self, *, charge_amount=100, deposit_amount=100):
        suffix = uuid4().hex[:10]
        with Session(engine) as db:
            state = db.get(BusinessDateState, 1)
            if state is None:
                db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime.utcnow()))
            else:
                state.current_business_date = date(2026, 9, 8)
                state.last_closed_at = None
            role = db.scalar(select(Role).where(Role.name == "b2_concurrency_admin"))
            if role is None:
                role = Role(name="b2_concurrency_admin")
                db.add(role)
                db.flush()
            user = User(username=f"b2-{suffix}", password_hash="test", role_id=role.id)
            guest = Guest(full_name=f"B2 Concurrency {suffix}")
            room_type = RoomType(name=f"B2 {suffix}", base_rate=100)
            db.add_all([user, guest, room_type])
            db.flush()
            room = Room(number=f"B{suffix[:7]}", room_type_id=room_type.id, status="occupied")
            reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="checked_in")
            db.add_all([room, reservation])
            db.flush()
            db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))
            folio = Folio(reservation_id=reservation.id, status="open")
            stay = Stay(reservation_id=reservation.id, room_id=room.id, status="in_house", check_in=reservation.check_in, check_out=reservation.check_out, deposit_required=deposit_amount, deposit_received=0)
            db.add_all([folio, stay])
            db.flush()

            if charge_amount:
                post_transaction(
                    db, transaction_type="folio_charge", description=f"B2 test charge {suffix}", reference_type="test_charge", reference_id=suffix,
                    folio_id=folio.id, reservation_id=reservation.id, created_by=user.id,
                    lines=[
                        {"account": "Guest Receivables", "direction": "debit", "amount": Decimal(charge_amount), "folio_id": folio.id},
                        {"account": "Revenue - Test", "direction": "credit", "amount": Decimal(charge_amount), "folio_id": folio.id},
                    ],
                )
            if deposit_amount:
                received = DepositTransaction(stay_id=stay.id, folio_id=folio.id, transaction_type="received", amount=Decimal(deposit_amount), payment_method="cash", reference=f"DEP-{suffix}", created_by=user.id)
                db.add(received)
                db.flush()
                post_transaction(
                    db, transaction_type="deposit_received", description=f"B2 test deposit {suffix}", reference_type="deposit", reference_id=str(received.id),
                    folio_id=folio.id, reservation_id=reservation.id, created_by=user.id,
                    lines=[
                        {"account": "Cash", "direction": "debit", "amount": Decimal(deposit_amount), "folio_id": folio.id, "stay_id": stay.id},
                        {"account": "Guest Deposits", "direction": "credit", "amount": Decimal(deposit_amount), "folio_id": folio.id, "stay_id": stay.id},
                    ],
                )
                stay.deposit_received = Decimal(deposit_amount)
            db.commit()

            payment_ids = []
            if charge_amount:
                for n in (1, 2):
                    payment = Payment(folio_id=folio.id, amount=Decimal("60.00"), method="cash", reference=f"PAY-{suffix}-{n}")
                    db.add(payment)
                    db.flush()
                    payment_ids.append(payment.id)
            refund_payment_id = None
            refund_ids = []
            if charge_amount:
                refund_payment = Payment(folio_id=folio.id, amount=Decimal("100.00"), method="cash", reference=f"REFUND-PAY-{suffix}")
                db.add(refund_payment)
                db.flush()
                post_transaction(
                    db, transaction_type="folio_payment", description=f"B2 refund seed payment {suffix}", reference_type="payment", reference_id=str(refund_payment.id),
                    folio_id=folio.id, reservation_id=reservation.id, created_by=user.id,
                    lines=[
                        {"account": "Cash", "direction": "debit", "amount": Decimal("100.00"), "folio_id": folio.id, "payment_method": "cash"},
                        {"account": "Guest Receivables", "direction": "credit", "amount": Decimal("100.00"), "folio_id": folio.id, "payment_method": "cash"},
                    ],
                )
                refund_payment_id = refund_payment.id
                for n in (1, 2):
                    refund = PaymentRefund(payment_id=refund_payment.id, folio_id=folio.id, amount=Decimal("70.00"), method="cash", reason=f"B2 race {n}", created_by=user.id)
                    db.add(refund)
                    db.flush()
                    refund_ids.append(refund.id)
            deposit_apply_ids = []
            if deposit_amount:
                for n in (1, 2):
                    apply = DepositTransaction(stay_id=stay.id, folio_id=folio.id, transaction_type="applied", amount=Decimal("70.00"), reference=f"APP-{suffix}-{n}", created_by=user.id)
                    db.add(apply)
                    db.flush()
                    deposit_apply_ids.append(apply.id)
            db.commit()
            return {"user_id": user.id, "folio_id": folio.id, "reservation_id": reservation.id, "stay_id": stay.id, "payment_ids": payment_ids, "refund_payment_id": refund_payment_id, "refund_ids": refund_ids, "deposit_apply_ids": deposit_apply_ids}

    def _run_two(self, fn1, fn2):
        barrier = Barrier(2)
        def run(fn):
            with Session(engine) as db:
                barrier.wait(timeout=20)
                try:
                    result = fn(db)
                    db.commit()
                    return ("ok", result)
                except HTTPException as exc:
                    db.rollback()
                    return ("rejected", exc.status_code, str(exc.detail))
        with ThreadPoolExecutor(max_workers=2) as pool:
            return pool.map(run, (fn1, fn2))

    def test_concurrent_payments_are_serialized(self):
        f = self._fixture(charge_amount=100, deposit_amount=0)
        def post_payment(db, pid):
            return post_transaction(db, transaction_type="folio_payment", description="B2 race payment", reference_type="payment", reference_id=str(pid), folio_id=f["folio_id"], reservation_id=f["reservation_id"], created_by=f["user_id"], lines=[{"account":"Cash","direction":"debit","amount":Decimal("60.00"),"folio_id":f["folio_id"]},{"account":"Guest Receivables","direction":"credit","amount":Decimal("60.00"),"folio_id":f["folio_id"]}]).id
        results = list(self._run_two(lambda db: post_payment(db, f["payment_ids"][0]), lambda db: post_payment(db, f["payment_ids"][1])))
        self.assertEqual(sum(r[0] == "ok" for r in results), 1)
        self.assertEqual(sum(r[0] == "rejected" for r in results), 1)
        with Session(engine) as db:
            self.assertEqual(folio_ledger_summary(db, f["folio_id"]).balance, Decimal("40.00"))

    def test_concurrent_refunds_are_serialized(self):
        f = self._fixture(charge_amount=100, deposit_amount=0)
        def post_refund(db, rid):
            return post_transaction(db, transaction_type="payment_refund", description="B2 race refund", reference_type="payment_refund", reference_id=str(rid), folio_id=f["folio_id"], reservation_id=f["reservation_id"], created_by=f["user_id"], lines=[{"account":"Guest Receivables","direction":"debit","amount":Decimal("70.00"),"folio_id":f["folio_id"]},{"account":"Cash","direction":"credit","amount":Decimal("70.00"),"folio_id":f["folio_id"],"payment_method":"cash"}]).id
        results = list(self._run_two(lambda db: post_refund(db, f["refund_ids"][0]), lambda db: post_refund(db, f["refund_ids"][1])))
        self.assertEqual(sum(r[0] == "ok" for r in results), 1)
        self.assertEqual(sum(r[0] == "rejected" for r in results), 1)
        with Session(engine) as db:
            posted = db.scalar(select(func.count()).select_from(FinancialTransaction).where(FinancialTransaction.folio_id == f["folio_id"], FinancialTransaction.transaction_type == "payment_refund", FinancialTransaction.status == "posted"))
            self.assertEqual(posted, 1)

    def test_concurrent_deposit_applications_are_serialized(self):
        f = self._fixture(charge_amount=0, deposit_amount=100)
        def post_apply(db, did):
            return post_transaction(db, transaction_type="deposit_applied", description="B2 race deposit apply", reference_type="deposit", reference_id=str(did), folio_id=f["folio_id"], reservation_id=f["reservation_id"], created_by=f["user_id"], lines=[{"account":"Guest Deposits","direction":"debit","amount":Decimal("70.00"),"folio_id":f["folio_id"],"stay_id":f["stay_id"]},{"account":"Guest Receivables","direction":"credit","amount":Decimal("70.00"),"folio_id":f["folio_id"],"stay_id":f["stay_id"]}]).id
        results = list(self._run_two(lambda db: post_apply(db, f["deposit_apply_ids"][0]), lambda db: post_apply(db, f["deposit_apply_ids"][1])))
        self.assertEqual(sum(r[0] == "ok" for r in results), 1)
        self.assertEqual(sum(r[0] == "rejected" for r in results), 1)
        with Session(engine) as db:
            credits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == f["stay_id"], LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted")) or Decimal("0.00")
            debits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == f["stay_id"], LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted")) or Decimal("0.00")
            self.assertEqual(Decimal(credits) - Decimal(debits), Decimal("30.00"))


if __name__ == "__main__":
    unittest.main()
