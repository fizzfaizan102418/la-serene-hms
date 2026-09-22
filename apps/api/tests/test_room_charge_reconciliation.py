import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
from app.business_date import get_current_business_date
from app.financial_authority import post_folio_charge_authoritative
from app.models import BusinessDateState, Folio, FolioItem, Guest, FinancialTransaction, LedgerEntry, Reservation, Room, RoomType, StayRateSegment
from app.pms_core import Stay
from app.room_charge_integrity import _room_charge_posted_for_date, post_accrued_room_charges


class RoomChargeReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _setup_two_night_stay(self, db: Session):
        db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 22), opened_at=datetime(2026, 9, 22, 5, 0, 0)))
        guest = Guest(full_name="Test Guest")
        room_type = RoomType(name="Test Room", base_rate=Decimal("10000.00"))
        db.add_all([guest, room_type])
        db.flush()
        room = Room(number="T1", room_type_id=room_type.id, status="occupied")
        db.add(room)
        db.flush()
        reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 21), check_out=date(2026, 9, 23), status="checked_in")
        db.add(reservation)
        db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        db.add(folio)
        db.flush()
        stay = Stay(
            reservation_id=reservation.id,
            room_id=room.id,
            guest_id=guest.id,
            status="checked_in",
            check_in=date(2026, 9, 21),
            check_out=date(2026, 9, 23),
            agreed_rate=Decimal("10000.00"),
            discount_amount=Decimal("0.00"),
        )
        db.add(stay)
        db.flush()
        db.add(StayRateSegment(stay_id=stay.id, from_date=date(2026, 9, 21), to_date=date(2026, 9, 23), rate=Decimal("10000.00"), discount_amount=Decimal("0.00"), source="reservation"))
        db.flush()
        return reservation, folio, stay

    def test_missing_second_night_is_posted_without_reposting_first_night(self):
        with Session(self.engine) as db:
            reservation, folio, stay = self._setup_two_night_stay(db)

            first_item = FolioItem(
                folio_id=folio.id,
                stay_id=stay.id,
                description="Room T1 · stay #1 · night 2026-09-21",
                category="room",
                quantity=Decimal("1"),
                unit_price=Decimal("10000.00"),
                discount=Decimal("0.00"),
            )
            db.add(first_item)
            db.flush()
            post_folio_charge_authoritative(
                db,
                folio_id=folio.id,
                reservation_id=reservation.id,
                item_id=first_item.id,
                amount=Decimal("10000.00"),
                stay_id=stay.id,
                category="room",
                created_by=1,
                gross_amount=Decimal("10000.00"),
                discount_amount=Decimal("0.00"),
            )
            db.commit()

            self.assertTrue(_room_charge_posted_for_date(db, folio.id, stay.id, date(2026, 9, 21)))
            self.assertFalse(_room_charge_posted_for_date(db, folio.id, stay.id, date(2026, 9, 22)))

            posted = post_accrued_room_charges(db, reservation, folio, date(2026, 9, 22), 1)
            db.commit()

            self.assertEqual(posted, 1)
            room_items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id, FolioItem.stay_id == stay.id, FolioItem.category == "room").order_by(FolioItem.id)).all()
            self.assertEqual(len(room_items), 2)
            self.assertEqual(sum(Decimal(item.quantity) for item in room_items), Decimal("2"))
            self.assertTrue(_room_charge_posted_for_date(db, folio.id, stay.id, date(2026, 9, 22)))

            room_transactions = db.scalars(
                select(FinancialTransaction)
                .where(
                    FinancialTransaction.folio_id == folio.id,
                    FinancialTransaction.transaction_type == "folio_charge",
                    FinancialTransaction.status == "posted",
                )
                .order_by(FinancialTransaction.id)
            ).all()
            self.assertEqual(len(room_transactions), 2)
            self.assertEqual([tx.business_date for tx in room_transactions], [date(2026, 9, 21), date(2026, 9, 22)])

    def test_reconciliation_is_idempotent_for_same_business_date(self):
        with Session(self.engine) as db:
            reservation, folio, stay = self._setup_two_night_stay(db)
            first = post_accrued_room_charges(db, reservation, folio, get_current_business_date(db), 1)
            db.commit()
            second = post_accrued_room_charges(db, reservation, folio, get_current_business_date(db), 1)
            db.commit()

            self.assertEqual(first, 2)
            self.assertEqual(second, 0)
            room_items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id, FolioItem.stay_id == stay.id, FolioItem.category == "room")).all()
            self.assertEqual(len(room_items), 2)


if __name__ == "__main__":
    unittest.main()
