import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine
# Register the full financial/PMS model metadata before exercising ledger ORM mappers.
import app.financial_authority  # noqa: F401
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
from app.pms_core import Stay  # noqa: F401
from app.financial_models import PaymentRefund
from app.financial_authority import folio_ledger_summary
from app.ledger import post_transaction, reverse_transaction
from app.models import (
    BusinessDateState,
    DepositTransaction,
    FinancialTransaction,
    Folio,
    Guest,
    LedgerEntry,
    Payment,
    Reservation,
    ReservationRoom,
    Room,
    RoomType,
)


class PostgreSQLSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if engine.dialect.name != "postgresql":
            raise unittest.SkipTest("HMS_DATABASE_URL is not PostgreSQL")

    def _business_state(self):
        with Session(engine) as db:
            state = db.get(BusinessDateState, 1)
            if state is None:
                state = BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime.utcnow())
                db.add(state)
            else:
                state.current_business_date = date(2026, 9, 8)
                state.last_closed_at = None
            db.commit()

    def _fixture(self, *, charge_amount=100, deposit_amount=100):
        self._business_state()
        suffix = uuid4().hex[:10]
        with Session(engine) as db:
            guest = Guest(full_name=f"CI Concurrency {suffix}")
            room_type = RoomType(name=f"Concurrency {suffix}", base_rate=100)
            db.add_all([guest, room_type])
            db.flush()
            room = Room(number=f"C{suffix[:6]}", room_type_id=room_type.id, status="occupied")
            reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 10), status="checked_in")
            db.add_all([room, reservation])
            db.flush()
            db.add(ReservationRoom(reservation_id=reservation.id, room_id=room.id))
            folio = Folio(reservation_id=reservation.id, status="open")
            db.add(folio)
            db.flush()
            stay = Stay(
                reservation_id=reservation.id,
                room_id=room.id,
                status="in_house",
                check_in=reservation.check_in,
                check_out=reservation.check_out,
                deposit_required=deposit_amount,
                deposit_received=0,
            )
            db.add(stay)
            db.flush()

            if charge_amount:
                charge = post_transaction(
                    db,
                    transaction_type="folio_charge",
                    description=f"CI concurrency charge {suffix}",
                    reference_type="test_charge",
                    reference_id=suffix,
                    folio_id=folio.id,
                    reservation_id=reservation.id,
                    created_by=None,
                    lines=[
                        {"account": "Guest Receivables", "direction": "debit", "amount": Decimal(charge_amount), "folio_id": folio.id},
                        {"account": "Revenue - Test", "direction": "credit", "amount": Decimal(charge_amount), "folio_id": folio.id},
                    ],
                )
                charge_id = charge.id
            else:
                charge_id = None

            if deposit_amount:
                received = DepositTransaction(
                    stay_id=stay.id,
                    folio_id=folio.id,
                    transaction_type="received",
                    amount=Decimal(deposit_amount),
                    payment_method="cash",
                    reference=f"DEP-{suffix}",
                    created_by=None,
                )
                db.add(received)
                db.flush()
                post_transaction(
                    db,
                    transaction_type="deposit_received",
                    description=f"CI concurrency deposit {suffix}",
                    reference_type="deposit",
                    reference_id=str(received.id),
                    folio_id=folio.id,
                    reservation_id=reservation.id,
                    created_by=None,
                    lines=[
                        {"account": "Cash", "direction": "debit", "amount": Decimal(deposit_amount), "folio_id": folio.id, "stay_id": stay.id},
                        {"account": "Guest Deposits", "direction": "credit", "amount": Decimal(deposit_amount), "folio_id": folio.id, "stay_id": stay.id},
                    ],
                )
                stay.deposit_received = Decimal(deposit_amount)
            db.commit()

            result = {"folio_id": folio.id, "reservation_id": reservation.id, "stay_id": stay.id, "suffix": suffix, "charge_id": charge_id}
            if charge_amount:
                p1 = Payment(folio_id=folio.id, amount=Decimal("60.00"), method="cash", reference=f"PAY-{suffix}-1")
                p2 = Payment(folio_id=folio.id, amount=Decimal("60.00"), method="cash", reference=f"PAY-{suffix}-2")
                db.add_all([p1, p2])
                db.flush()
                result["payment_ids"] = (p1.id, p2.id)
            if deposit_amount:
                a1 = DepositTransaction(stay_id=stay.id, folio_id=folio.id, transaction_type="applied", amount=Decimal("70.00"), reference=f"APP-{suffix}-1", created_by=None)
                a2 = DepositTransaction(stay_id=stay.id, folio_id=folio.id, transaction_type="applied", amount=Decimal("70.00"), reference=f"APP-{suffix}-2", created_by=None)
                db.add_all([a1, a2])
                db.flush()
                result["deposit_apply_ids"] = (a1.id, a2.id)
            db.commit()
            return result

    def test_expected_tables_exist(self):
        names = set(inspect(engine).get_table_names())
        for expected in (
            "stays", "stay_occupants", "stay_rate_segments", "deposit_transactions",
            "room_moves", "reservation_splits", "business_date_state",
            "financial_transactions", "ledger_entries",
        ):
            self.assertIn(expected, names)

    def test_ledger_transaction_is_balanced_and_persistent(self):
        with Session(engine) as db:
            state = db.get(BusinessDateState, 1)
            if state is None:
                state = BusinessDateState(id=1, current_business_date=date(2026, 9, 8))
                db.add(state)
                db.flush()
            tx = post_transaction(
                db,
                transaction_type="ci_smoke",
                description="CI PostgreSQL ledger smoke test",
                created_by=None,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("10.00")},
                    {"account": "Test Revenue", "direction": "credit", "amount": Decimal("10.00")},
                ],
            )
            transaction_id = tx.id
            db.commit()

        with Session(engine) as db:
            tx = db.get(FinancialTransaction, transaction_id)
            self.assertIsNotNone(tx)
            entries = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == transaction_id)).all()
            self.assertEqual(len(entries), 2)
            self.assertEqual(sum((e.amount for e in entries if e.direction == "debit"), Decimal("0.00")), Decimal("10.00"))
            self.assertEqual(sum((e.amount for e in entries if e.direction == "credit"), Decimal("0.00")), Decimal("10.00"))

    def test_ledger_entries_cannot_be_updated_or_deleted(self):
        with Session(engine) as db:
            tx = post_transaction(
                db,
                transaction_type="ci_immutability",
                description="CI PostgreSQL ledger immutability test",
                created_by=None,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("7.00")},
                    {"account": "Test Revenue", "direction": "credit", "amount": Decimal("7.00")},
                ],
            )
            db.commit()
            entry = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id).order_by(LedgerEntry.id)).first()
            self.assertIsNotNone(entry)

            entry.amount = Decimal("8.00")
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()

            entry = db.get(LedgerEntry, entry.id)
            db.delete(entry)
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()

    def test_financial_transaction_can_only_transition_to_reversed(self):
        with Session(engine) as db:
            tx = post_transaction(
                db,
                transaction_type="ci_reversal",
                description="CI PostgreSQL reversal test",
                created_by=None,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("5.00")},
                    {"account": "Test Revenue", "direction": "credit", "amount": Decimal("5.00")},
                ],
            )
            db.commit()
            tx_id = tx.id

            reversal = reverse_transaction(db, transaction_id=tx_id, created_by=None, reason="CI test correction")
            db.commit()
            db.refresh(tx)
            self.assertEqual(tx.status, "reversed")
            self.assertEqual(reversal.reversal_of_id, tx_id)

            tx.description = "tampered"
            with self.assertRaises(DBAPIError):
                db.flush()
            db.rollback()

    def _concurrent_postings(self, workers):
        barrier = Barrier(len(workers))

        def run(worker):
            with Session(engine) as db:
                barrier.wait(timeout=20)
                try:
                    tx_id = worker(db)
                    db.commit()
                    return ("ok", tx_id)
                except HTTPException as exc:
                    db.rollback()
                    return ("rejected", exc.status_code, str(exc.detail))
                except Exception:
                    db.rollback()
                    raise

        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            return list(pool.map(run, workers))

    def test_concurrent_payments_cannot_consume_same_folio_balance(self):
        fixture = self._fixture(charge_amount=100, deposit_amount=0)
        payment_ids = fixture["payment_ids"]

        def post_payment(db, payment_id):
            tx = post_transaction(
                db,
                transaction_type="folio_payment",
                description=f"CI concurrent payment {payment_id}",
                reference_type="payment",
                reference_id=str(payment_id),
                folio_id=fixture["folio_id"],
                reservation_id=fixture["reservation_id"],
                created_by=None,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("60.00"), "folio_id": fixture["folio_id"], "payment_method": "cash"},
                    {"account": "Guest Receivables", "direction": "credit", "amount": Decimal("60.00"), "folio_id": fixture["folio_id"], "payment_method": "cash"},
                ],
            )
            return tx.id

        results = self._concurrent_postings([lambda db: post_payment(db, payment_ids[0]), lambda db: post_payment(db, payment_ids[1])])
        self.assertEqual([item[0] for item in results].count("ok"), 1)
        self.assertEqual([item[0] for item in results].count("rejected"), 1)
        with Session(engine) as db:
            posted = db.scalar(select(func.count()).select_from(FinancialTransaction).where(FinancialTransaction.folio_id == fixture["folio_id"], FinancialTransaction.transaction_type == "folio_payment", FinancialTransaction.status == "posted"))
            self.assertEqual(posted, 1)
            self.assertEqual(folio_ledger_summary(db, fixture["folio_id"]).balance, Decimal("40.00"))

    def test_concurrent_refunds_cannot_exceed_payment_amount(self):
        fixture = self._fixture(charge_amount=100, deposit_amount=0)
        with Session(engine) as db:
            payment = db.get(Payment, fixture["payment_ids"][0])
            post_transaction(
                db,
                transaction_type="folio_payment",
                description=f"CI refund payment {payment.id}",
                reference_type="payment",
                reference_id=str(payment.id),
                folio_id=fixture["folio_id"],
                reservation_id=fixture["reservation_id"],
                created_by=None,
                lines=[
                    {"account": "Cash", "direction": "debit", "amount": Decimal("100.00"), "folio_id": fixture["folio_id"], "payment_method": "cash"},
                    {"account": "Guest Receivables", "direction": "credit", "amount": Decimal("100.00"), "folio_id": fixture["folio_id"], "payment_method": "cash"},
                ],
            )
            r1 = PaymentRefund(payment_id=payment.id, folio_id=fixture["folio_id"], amount=Decimal("70.00"), method="cash", reason="CI race 1", created_by=None)
            r2 = PaymentRefund(payment_id=payment.id, folio_id=fixture["folio_id"], amount=Decimal("70.00"), method="cash", reason="CI race 2", created_by=None)
            db.add_all([r1, r2])
            db.flush()
            refund_ids = (r1.id, r2.id)
            db.commit()

        def post_refund(db, refund_id):
            tx = post_transaction(
                db,
                transaction_type="payment_refund",
                description=f"CI concurrent refund {refund_id}",
                reference_type="payment_refund",
                reference_id=str(refund_id),
                folio_id=fixture["folio_id"],
                reservation_id=fixture["reservation_id"],
                created_by=None,
                lines=[
                    {"account": "Guest Receivables", "direction": "debit", "amount": Decimal("70.00"), "folio_id": fixture["folio_id"]},
                    {"account": "Cash", "direction": "credit", "amount": Decimal("70.00"), "folio_id": fixture["folio_id"], "payment_method": "cash"},
                ],
            )
            return tx.id

        results = self._concurrent_postings([lambda db: post_refund(db, refund_ids[0]), lambda db: post_refund(db, refund_ids[1])])
        self.assertEqual([item[0] for item in results].count("ok"), 1)
        self.assertEqual([item[0] for item in results].count("rejected"), 1)
        with Session(engine) as db:
            posted = db.scalar(select(func.count()).select_from(FinancialTransaction).where(FinancialTransaction.folio_id == fixture["folio_id"], FinancialTransaction.transaction_type == "payment_refund", FinancialTransaction.status == "posted"))
            self.assertEqual(posted, 1)
            receivable = folio_ledger_summary(db, fixture["folio_id"])
            self.assertEqual(receivable.paid, Decimal("30.00"))
            self.assertEqual(receivable.balance, Decimal("70.00"))

    def test_concurrent_deposit_applications_cannot_double_spend_balance(self):
        fixture = self._fixture(charge_amount=0, deposit_amount=100)
        apply_ids = fixture["deposit_apply_ids"]

        def post_application(db, deposit_id):
            tx = post_transaction(
                db,
                transaction_type="deposit_applied",
                description=f"CI concurrent deposit application {deposit_id}",
                reference_type="deposit",
                reference_id=str(deposit_id),
                folio_id=fixture["folio_id"],
                reservation_id=fixture["reservation_id"],
                created_by=None,
                lines=[
                    {"account": "Guest Deposits", "direction": "debit", "amount": Decimal("70.00"), "stay_id": fixture["stay_id"], "folio_id": fixture["folio_id"]},
                    {"account": "Guest Receivables", "direction": "credit", "amount": Decimal("70.00"), "stay_id": fixture["stay_id"], "folio_id": fixture["folio_id"]},
                ],
            )
            return tx.id

        results = self._concurrent_postings([lambda db: post_application(db, apply_ids[0]), lambda db: post_application(db, apply_ids[1])])
        self.assertEqual([item[0] for item in results].count("ok"), 1)
        self.assertEqual([item[0] for item in results].count("rejected"), 1)
        with Session(engine) as db:
            posted = db.scalar(select(func.count()).select_from(FinancialTransaction).where(FinancialTransaction.reservation_id == fixture["reservation_id"], FinancialTransaction.transaction_type == "deposit_applied", FinancialTransaction.status == "posted"))
            self.assertEqual(posted, 1)
            credits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == fixture["stay_id"], LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted")) or Decimal("0.00")
            debits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == fixture["stay_id"], LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted")) or Decimal("0.00")
            self.assertEqual(Decimal(credits) - Decimal(debits), Decimal("30.00"))


if __name__ == "__main__":
    unittest.main()
