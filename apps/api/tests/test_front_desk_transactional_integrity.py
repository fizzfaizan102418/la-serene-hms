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
from app.front_desk import atomic_check_in, atomic_checkout
from app.ledger import post_transaction
from app.models import BusinessDateState, FinancialTransaction, Folio, FolioItem, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.pms_core import Stay


class FrontDeskTransactionalIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.business_date = date(2026, 9, 8)
        self.db.add(BusinessDateState(id=1, current_business_date=self.business_date, opened_at=datetime(2026, 9, 8)))
        role = Role(name="reception")
        guest = Guest(full_name="Transactional Guest")
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add_all([role, guest, room_type]); self.db.flush()
        self.user = User(username="reception", password_hash="test", role_id=role.id)
        self.room = Room(number="101", room_type_id=room_type.id, status="available")
        self.db.add_all([self.user, self.room]); self.db.commit()
        self.guest = guest
        self.room_type = room_type

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def make_reservation(self, check_in: date, check_out: date, status: str = "reserved"):
        reservation = Reservation(guest_id=self.guest.id, check_in=check_in, check_out=check_out, status=status)
        self.db.add(reservation); self.db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=self.room.id))
        self.db.commit()
        return reservation, folio

    def make_checked_in_reservation(self, check_in: date, check_out: date, rate: Decimal = Decimal("100")):
        reservation, folio = self.make_reservation(check_in, check_out, status="checked_in")
        stay = Stay(
            reservation_id=reservation.id,
            room_id=self.room.id,
            guest_id=self.guest.id,
            status="checked_in",
            check_in=check_in,
            check_out=check_out,
            actual_check_in=datetime(2026, 9, 8, 14, 0),
            agreed_rate=rate,
            discount_percent=Decimal("0"),
            discount_amount=Decimal("0"),
        )
        self.db.add(stay)
        self.room.status = "occupied"
        self.db.commit()
        return reservation, folio, stay

    def test_check_in_uses_persisted_business_date_and_aligns_entities(self):
        reservation, folio = self.make_reservation(date(2026, 9, 8), date(2026, 9, 10))
        result = atomic_check_in(reservation.id, self.db, self.user)
        self.assertEqual(result["business_date"], self.business_date)
        self.assertEqual(self.db.get(Reservation, reservation.id).status, "checked_in")
        self.assertEqual(self.db.get(Room, self.room.id).status, "occupied")
        stay = self.db.scalar(select(Stay).where(Stay.reservation_id == reservation.id))
        self.assertIsNotNone(stay)
        self.assertEqual(stay.status, "checked_in")
        self.assertEqual(stay.check_in, reservation.check_in)
        self.assertEqual(stay.check_out, reservation.check_out)
        self.assertEqual(self.db.get(Folio, folio.id).status, "open")

    def test_check_in_rejects_future_arrival_against_business_date(self):
        reservation, _ = self.make_reservation(date(2026, 9, 9), date(2026, 9, 10))
        with self.assertRaises(HTTPException) as ctx:
            atomic_check_in(reservation.id, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        self.assertEqual(self.db.get(Reservation, reservation.id).status, "reserved")
        self.assertEqual(self.db.get(Room, self.room.id).status, "available")

    def test_checkout_posts_only_elapsed_business_date_room_nights_then_closes(self):
        reservation, folio, _ = self.make_checked_in_reservation(date(2026, 9, 7), date(2026, 9, 10))

        with self.assertRaises(HTTPException) as ctx:
            atomic_checkout(reservation.id, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()

        self.assertEqual(self.db.get(Reservation, reservation.id).status, "checked_in")
        self.assertEqual(self.db.get(Room, self.room.id).status, "occupied")
        self.assertEqual(self.db.scalar(select(FolioItem.id).where(FolioItem.folio_id == folio.id)), None)
        self.assertEqual(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.folio_id == folio.id)), None)

        payment = __import__("app.models", fromlist=["Payment"]).Payment(folio_id=folio.id, amount=Decimal("100"), method="cash")
        self.db.add(payment); self.db.flush()
        post_transaction(
            self.db,
            transaction_type="folio_payment",
            description="Checkout settlement",
            reference_type="payment",
            reference_id=str(payment.id),
            folio_id=folio.id,
            reservation_id=reservation.id,
            created_by=self.user.id,
            idempotency_key=f"checkout-test-payment:{payment.id}",
            lines=[
                {"account": "Cash", "direction": "debit", "amount": Decimal("100"), "folio_id": folio.id, "payment_method": "cash"},
                {"account": "Guest Receivables", "direction": "credit", "amount": Decimal("100"), "folio_id": folio.id, "payment_method": "cash"},
            ],
        )
        self.db.commit()

        result = atomic_checkout(reservation.id, self.db, self.user)
        self.assertEqual(result["business_date"], self.business_date)
        self.assertEqual(result["room_charges_posted"], 1)
        self.assertEqual(self.db.get(Reservation, reservation.id).status, "checked_out")
        self.assertEqual(self.db.get(Folio, folio.id).status, "closed")
        self.assertEqual(self.db.get(Room, self.room.id).status, "dirty")
        charge = self.db.scalar(select(FolioItem).where(FolioItem.folio_id == folio.id, FolioItem.category == "room"))
        self.assertIsNotNone(charge)
        self.assertEqual(Decimal(charge.quantity), Decimal("1"))
        self.assertEqual(Decimal(charge.unit_price), Decimal("100.00"))
        txs = self.db.scalars(select(FinancialTransaction).where(FinancialTransaction.folio_id == folio.id).order_by(FinancialTransaction.id)).all()
        self.assertTrue(any(tx.transaction_type == "folio_charge" and tx.business_date == self.business_date for tx in txs))
        self.assertTrue(any(tx.transaction_type == "folio_payment" and tx.business_date == self.business_date for tx in txs))

    def test_checkout_rejects_room_occupancy_drift_before_financial_posting(self):
        reservation, folio = self.make_reservation(date(2026, 9, 8), date(2026, 9, 10), status="checked_in")
        with self.assertRaises(HTTPException) as ctx:
            atomic_checkout(reservation.id, self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()
        self.assertEqual(self.db.scalar(select(FolioItem.id).where(FolioItem.folio_id == folio.id)), None)
        self.assertEqual(self.db.get(Reservation, reservation.id).status, "checked_in")


if __name__ == "__main__":
    unittest.main()
