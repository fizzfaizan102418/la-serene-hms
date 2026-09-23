import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
from app.business_date import get_current_business_date
from app.financial_authority import post_folio_charge_authoritative
from app.models import (
    BusinessDateState,
    FinancialTransaction,
    Folio,
    FolioItem,
    Guest,
    Reservation,
    Room,
    RoomType,
    StayRateSegment,
)
from app.pms_core import Stay
from app.room_charge_integrity import _room_charge_posted_for_date, post_accrued_room_charges


class RoomChargeReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _setup_reservation(self, db: Session, room_numbers=("T1",), check_out=date(2026, 9, 23)):
        db.add(
            BusinessDateState(
                id=1,
                current_business_date=date(2026, 9, 22),
                opened_at=datetime(2026, 9, 22, 5, 0, 0),
            )
        )
        guest = Guest(full_name="Test Guest")
        room_type = RoomType(name="Test Room", base_rate=Decimal("10000.00"))
        db.add_all([guest, room_type])
        db.flush()

        rooms = []
        for number in room_numbers:
            room = Room(number=number, room_type_id=room_type.id, status="occupied")
            db.add(room)
            db.flush()
            rooms.append(room)

        reservation = Reservation(
            guest_id=guest.id,
            check_in=date(2026, 9, 21),
            check_out=check_out,
            status="checked_in",
        )
        db.add(reservation)
        db.flush()

        folio = Folio(reservation_id=reservation.id, status="open")
        db.add(folio)
        db.flush()

        stays = []
        for room in rooms:
            stay = Stay(
                reservation_id=reservation.id,
                room_id=room.id,
                guest_id=guest.id,
                status="checked_in",
                check_in=date(2026, 9, 21),
                check_out=check_out,
                agreed_rate=Decimal("10000.00"),
                discount_amount=Decimal("0.00"),
            )
            db.add(stay)
            db.flush()
            db.add(
                StayRateSegment(
                    stay_id=stay.id,
                    from_date=date(2026, 9, 21),
                    to_date=check_out,
                    rate=Decimal("10000.00"),
                    discount_amount=Decimal("0.00"),
                    source="reservation",
                )
            )
            db.flush()
            stays.append(stay)

        return reservation, folio, stays

    def _post_night(self, db, reservation, folio, stay, posting_date):
        room = db.get(Room, stay.room_id)
        item = FolioItem(
            folio_id=folio.id,
            stay_id=stay.id,
            description=f"Room {room.number} · stay #{stay.id} · night {posting_date}",
            category="room",
            quantity=Decimal("1"),
            unit_price=Decimal("10000.00"),
            discount=Decimal("0.00"),
        )
        db.add(item)
        db.flush()
        db.get(BusinessDateState, 1).current_business_date = posting_date
        db.flush()
        post_folio_charge_authoritative(
            db,
            folio_id=folio.id,
            reservation_id=reservation.id,
            item_id=item.id,
            amount=Decimal("10000.00"),
            stay_id=stay.id,
            category="room",
            created_by=1,
            gross_amount=Decimal("10000.00"),
            discount_amount=Decimal("0.00"),
        )
        db.flush()

    def test_missing_second_night_is_posted_without_reposting_first_night(self):
        with Session(self.engine) as db:
            reservation, folio, stays = self._setup_reservation(db)
            stay = stays[0]

            self._post_night(db, reservation, folio, stay, date(2026, 9, 21))
            db.commit()

            self.assertTrue(
                _room_charge_posted_for_date(
                    db, folio.id, stay.id, date(2026, 9, 21)
                )
            )

            db.get(BusinessDateState, 1).current_business_date = date(2026, 9, 22)
            db.flush()
            posted = post_accrued_room_charges(
                db, reservation, folio, date(2026, 9, 22), 1
            )
            db.commit()

            self.assertEqual(posted, 1)
            room_items = db.scalars(
                select(FolioItem).where(
                    FolioItem.folio_id == folio.id,
                    FolioItem.stay_id == stay.id,
                    FolioItem.category == "room",
                ).order_by(FolioItem.id)
            ).all()
            self.assertEqual(len(room_items), 2)
            self.assertTrue(
                _room_charge_posted_for_date(
                    db, folio.id, stay.id, date(2026, 9, 22)
                )
            )

    def test_two_room_extension_posts_missing_night_for_each_stay(self):
        with Session(self.engine) as db:
            reservation, folio, stays = self._setup_reservation(
                db, room_numbers=("T1", "T2")
            )

            # Both rooms have their first night. Only Room T2 has the extension night.
            for stay in stays:
                self._post_night(db, reservation, folio, stay, date(2026, 9, 21))
            self._post_night(db, reservation, folio, stays[1], date(2026, 9, 22))
            db.commit()

            posted = post_accrued_room_charges(
                db, reservation, folio, date(2026, 9, 22), 1
            )
            db.commit()

            # Only Room T1's missing extension night is added.
            self.assertEqual(posted, 1)
            for stay in stays:
                self.assertTrue(
                    _room_charge_posted_for_date(
                        db, folio.id, stay.id, date(2026, 9, 21)
                    )
                )
                self.assertTrue(
                    _room_charge_posted_for_date(
                        db, folio.id, stay.id, date(2026, 9, 22)
                    )
                )

            room_transactions = db.scalars(
                select(FinancialTransaction).where(
                    FinancialTransaction.folio_id == folio.id,
                    FinancialTransaction.transaction_type == "folio_charge",
                    FinancialTransaction.status == "posted",
                ).order_by(FinancialTransaction.id)
            ).all()
            self.assertEqual(len(room_transactions), 4)
            self.assertEqual(
                [tx.business_date for tx in room_transactions],
                [
                    date(2026, 9, 21),
                    date(2026, 9, 21),
                    date(2026, 9, 22),
                    date(2026, 9, 22),
                ],
            )

    def test_deterministic_room_night_key_blocks_legacy_duplicate_repost(self):
        with Session(self.engine) as db:
            reservation, folio, stays = self._setup_reservation(db)
            stay = stays[0]
            posting_date = date(2026, 9, 22)

            # Simulate a legacy posting whose folio-item linkage cannot satisfy
            # the exact stay/date lookup, but whose deterministic room-night key
            # identifies the night as already posted.
            item = FolioItem(
                folio_id=folio.id,
                stay_id=None,
                description="Legacy room posting",
                category="room",
                quantity=Decimal("1"),
                unit_price=Decimal("10000.00"),
                discount=Decimal("0.00"),
            )
            db.add(item)
            db.flush()
            db.get(BusinessDateState, 1).current_business_date = posting_date
            post_folio_charge_authoritative(
                db,
                folio_id=folio.id,
                reservation_id=reservation.id,
                item_id=item.id,
                amount=Decimal("10000.00"),
                stay_id=None,
                category="room",
                created_by=1,
                gross_amount=Decimal("10000.00"),
                discount_amount=Decimal("0.00"),
                idempotency_key=f"room-night:{folio.id}:{stay.id}:{posting_date.isoformat()}",
            )
            db.commit()

            posted = post_accrued_room_charges(
                db, reservation, folio, posting_date, 1
            )
            db.commit()

            self.assertEqual(posted, 0)
            room_items = db.scalars(
                select(FolioItem).where(
                    FolioItem.folio_id == folio.id,
                    FolioItem.category == "room",
                )
            ).all()
            self.assertEqual(len(room_items), 1)


    def test_checkout_date_does_not_post_third_room_night(self):
        with Session(self.engine) as db:
            reservation, folio, stays = self._setup_reservation(
                db, room_numbers=("T1", "T2"), check_out=date(2026, 9, 23)
            )

            # Both rooms already have their two contracted nights covered.
            for stay in stays:
                self._post_night(db, reservation, folio, stay, date(2026, 9, 21))
                self._post_night(db, reservation, folio, stay, date(2026, 9, 22))
            db.commit()

            posted = post_accrued_room_charges(
                db, reservation, folio, date(2026, 9, 23), 1
            )
            db.commit()

            self.assertEqual(posted, 0)
            room_items = db.scalars(
                select(FolioItem).where(
                    FolioItem.folio_id == folio.id,
                    FolioItem.category == "room",
                )
            ).all()
            self.assertEqual(len(room_items), 4)

    def test_reconciliation_is_idempotent_for_same_business_date(self):
        with Session(self.engine) as db:
            reservation, folio, stays = self._setup_reservation(db)
            first = post_accrued_room_charges(
                db, reservation, folio, get_current_business_date(db), 1
            )
            db.commit()

            second = post_accrued_room_charges(
                db, reservation, folio, get_current_business_date(db), 1
            )
            db.commit()

            self.assertEqual(first, 1)
            self.assertEqual(second, 0)
            room_items = db.scalars(
                select(FolioItem).where(
                    FolioItem.folio_id == folio.id,
                    FolioItem.category == "room",
                )
            ).all()
            self.assertEqual(len(room_items), 1)


if __name__ == "__main__":
    unittest.main()
