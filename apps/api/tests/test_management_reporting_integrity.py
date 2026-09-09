import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.ledger import post_transaction
from app.models import BusinessDateState, Folio, FolioItem, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.reports import management_report


class ManagementReportingIntegrityTests(unittest.TestCase):
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
        user = User(username="admin", password_hash="test", role_id=role.id)
        guest = Guest(full_name="Management Guest")
        room_type = RoomType(name="Standard", base_rate=100)
        room_a = Room(number="301", room_type_id=room_type.id, status="occupied")
        room_b = Room(number="302", room_type_id=room_type.id, status="available")
        reservation = Reservation(
            guest_id=guest.id,
            check_in=date(2026, 9, 9),
            check_out=date(2026, 9, 10),
            status="checked_in",
            checked_in_at=datetime(2026, 9, 9, 12, 0),
        )
        self.db.add_all([user, guest, room_type, room_a, room_b, reservation])
        self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=room_a.id))
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.flush()
        item = FolioItem(folio_id=folio.id, description="Room charge", category="room", quantity=1, unit_price=100, discount=10)
        self.db.add(item)
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 9), opened_at=datetime(2026, 9, 9, 8, 0)))
        self.db.commit()

        post_transaction(
            self.db,
            transaction_type="folio_charge",
            description="Room charge",
            reference_type="folio_item",
            reference_id=str(item.id),
            folio_id=folio.id,
            reservation_id=reservation.id,
            created_by=user.id,
            idempotency_key=f"management-room:{item.id}",
            business_date=date(2026, 9, 9),
            lines=[
                {"account": "Guest Receivables", "direction": "debit", "amount": Decimal("100.00"), "folio_id": folio.id, "reservation_id": reservation.id},
                {"account": "Revenue - room", "direction": "credit", "amount": Decimal("100.00"), "folio_id": folio.id, "reservation_id": reservation.id},
            ],
        )
        post_transaction(
            self.db,
            transaction_type="folio_discount",
            description="Room discount",
            reference_type="folio_item_discount",
            reference_id=str(item.id),
            folio_id=folio.id,
            reservation_id=reservation.id,
            created_by=user.id,
            idempotency_key=f"management-discount:{item.id}",
            business_date=date(2026, 9, 9),
            lines=[
                {"account": "Revenue - room", "direction": "debit", "amount": Decimal("10.00"), "folio_id": folio.id, "reservation_id": reservation.id},
                {"account": "Guest Receivables", "direction": "credit", "amount": Decimal("10.00"), "folio_id": folio.id, "reservation_id": reservation.id},
            ],
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_management_report_uses_persisted_business_date(self):
        report = management_report(self.db)
        self.assertEqual(report["business_date"], date(2026, 9, 9))
        self.assertEqual(report["rooms"]["total"], 2)
        self.assertEqual(report["occupancy"]["occupied_room_nights"], 1)
        self.assertEqual(report["occupancy"]["available_room_nights"], 2)
        self.assertEqual(report["occupancy"]["occupancy_rate"], 50.0)

    def test_management_report_uses_ledger_authority_for_revenue(self):
        report = management_report(self.db)
        self.assertEqual(report["revenue"]["room"], Decimal("90.00"))
        self.assertEqual(report["revenue"]["total"], Decimal("90.00"))
        self.assertEqual(report["finance"]["reconciliation_status"], "balanced")
        self.assertEqual(report["finance"]["revenue_difference"], Decimal("0.00"))

    def test_management_report_does_not_depend_on_folio_item_created_at(self):
        item = self.db.query(FolioItem).one()
        item.created_at = datetime(2026, 9, 1, 10, 0)
        self.db.commit()
        report = management_report(self.db)
        self.assertEqual(report["revenue"]["room"], Decimal("90.00"))


if __name__ == "__main__":
    unittest.main()
