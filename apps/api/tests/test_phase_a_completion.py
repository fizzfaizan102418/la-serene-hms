import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import BusinessDateState, DepositTransaction, FinancialTransaction, Folio, FolioItem, Guest, Payment, Reservation, ReservationRoom, Role, Room, RoomType, StayOccupant, User
from app.phase_a_completion import (
    DepositApplyCreate,
    DepositRefundCreate,
    DepositTransferCreate,
    FolioItemRouteCreate,
    FolioWindowUpdatePayload,
    OccupantGuestChange,
    StayCheckInPayload,
    apply_deposit,
    change_stay_occupant_guest,
    check_in_stay,
    refund_deposit,
    route_folio_item,
    share_stay_with_occupant,
    transfer_deposit,
    update_folio_window,
)
from app.pms_core import Stay
from app.stay_lifecycle import StayFolioWindow


class PhaseACompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=cls.engine)

    def setUp(self):
        self.db = Session(self.engine)
        role = Role(name=f"phase-a-{id(self)}")
        self.db.add(role)
        self.db.flush()
        self.user = User(username=f"phase-a-{id(self)}", password_hash="test", role_id=role.id)
        self.db.add(self.user)
        self.db.flush()

        guests = [Guest(full_name="Booking Guest"), Guest(full_name="Ahmed"), Guest(full_name="Bilal"), Guest(full_name="Usman")]
        self.db.add_all(guests)
        self.db.flush()
        room_type = RoomType(name=f"PhaseRoom-{id(self)}", base_rate=150)
        self.db.add(room_type)
        self.db.flush()
        rooms = [Room(number=f"PA-{id(self)}-101", room_type_id=room_type.id, status="reserved"), Room(number=f"PA-{id(self)}-102", room_type_id=room_type.id, status="occupied"), Room(number=f"PA-{id(self)}-103", room_type_id=room_type.id, status="available")]
        self.db.add_all(rooms)
        self.db.flush()
        reservation = Reservation(guest_id=guests[0].id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 12), status="reserved")
        self.db.add(reservation)
        self.db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=rooms[0].id))
        stay = Stay(reservation_id=reservation.id, room_id=rooms[0].id, guest_id=guests[1].id, status="reserved", check_in=reservation.check_in, check_out=reservation.check_out, agreed_rate=Decimal("150.00"), deposit_required=Decimal("300.00"), deposit_received=Decimal("200.00"))
        self.db.add(stay)
        self.db.flush()
        self.db.add(StayOccupant(stay_id=stay.id, guest_id=guests[1].id, role="primary", is_primary=True, check_in=stay.check_in, check_out=stay.check_out))
        window = StayFolioWindow(folio_id=folio.id, stay_id=stay.id, name="Room charges", payer_type="guest", guest_id=guests[1].id, status="open")
        self.db.add(window)
        self.db.add(DepositTransaction(stay_id=stay.id, transaction_type="received", amount=Decimal("200.00"), payment_method="cash", reference="DEP-SEED", created_by=self.user.id))
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8)))
        self.db.commit()
        self.db.expire_all()
        self.reservation = self.db.get(Reservation, reservation.id)
        self.stay = self.db.get(Stay, stay.id)
        self.room = self.db.get(Room, rooms[0].id)
        self.room_2 = self.db.get(Room, rooms[2].id)
        self.folio = self.db.get(Folio, folio.id)
        self.window = self.db.scalar(select(StayFolioWindow).where(StayFolioWindow.stay_id == stay.id))
        self.guest_ahmed = self.db.get(Guest, guests[1].id)
        self.guest_bilal = self.db.get(Guest, guests[2].id)
        self.guest_usman = self.db.get(Guest, guests[3].id)
        self.user = self.db.get(User, self.user.id)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_room_level_checkin_and_room_sharing(self):
        result = check_in_stay(self.stay.id, StayCheckInPayload(reason="Room 101 arrival"), self.db, self.user)
        self.assertEqual(result["status"], "checked_in")
        self.assertEqual(self.db.get(Room, self.room.id).status, "occupied")
        share = share_stay_with_occupant(self.stay.id, OccupantGuestChange(guest_id=self.guest_bilal.id), self.db, self.user)
        self.assertFalse(share["is_primary"])
        occupants = self.db.scalars(select(StayOccupant).where(StayOccupant.stay_id == self.stay.id).order_by(StayOccupant.id)).all()
        self.assertEqual(len(occupants), 2)
        changed = change_stay_occupant_guest(self.stay.id, share["id"], OccupantGuestChange(guest_id=self.guest_usman.id, reason="Guest replacement"), self.db, self.user)
        self.assertEqual(changed["guest_id"], self.guest_usman.id)

    def test_deposit_transfer_refund_and_apply_to_folio(self):
        other_reservation = Reservation(guest_id=self.guest_ahmed.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 12), status="reserved")
        self.db.add(other_reservation)
        self.db.flush()
        other_folio = Folio(reservation_id=other_reservation.id, status="open")
        self.db.add(other_folio)
        self.db.flush()
        other_stay = Stay(reservation_id=other_reservation.id, room_id=self.room_2.id, guest_id=self.guest_ahmed.id, status="reserved", check_in=other_reservation.check_in, check_out=other_reservation.check_out, agreed_rate=Decimal("180.00"))
        self.db.add(other_stay)
        self.db.commit()
        self.db.expire_all()
        other_stay = self.db.get(Stay, other_stay.id)

        transferred = transfer_deposit(self.stay.id, DepositTransferCreate(target_stay_id=other_stay.id, amount=Decimal("50.00"), reason="Room split"), self.db, self.user)
        self.assertEqual(transferred["source_balance"], Decimal("150.00"))
        self.assertEqual(transferred["target_balance"], Decimal("50.00"))

        refunded = refund_deposit(self.stay.id, DepositRefundCreate(amount=Decimal("25.00"), payment_method="cash", reason="Guest request"), self.db, self.user)
        self.assertEqual(refunded["balance"], Decimal("125.00"))
        self.assertTrue(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.transaction_type == "deposit_refund")))

        apply_result = apply_deposit(self.stay.id, DepositApplyCreate(folio_id=self.folio.id, amount=Decimal("100.00"), reason="Apply on arrival"), self.db, self.user)
        self.assertEqual(apply_result["remaining_deposit"], Decimal("25.00"))
        payment = self.db.scalar(select(Payment).where(Payment.folio_id == self.folio.id))
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal("100.00"))
        self.assertEqual(payment.method, "deposit")
        self.assertTrue(self.db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.transaction_type == "deposit_applied")))

    def test_folio_item_routing_and_window_lifecycle(self):
        item = FolioItem(folio_id=self.folio.id, stay_id=self.stay.id, description="Room minibar", category="minibar", quantity=Decimal("1"), unit_price=Decimal("40.00"), discount=Decimal("0.00"))
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        routed = route_folio_item(item.id, FolioItemRouteCreate(window_id=self.window.id, notes="Primary room window"), self.db, self.user)
        self.assertEqual(routed["window_id"], self.window.id)
        updated = update_folio_window(self.stay.id, self.window.id, FolioWindowUpdatePayload(name="Incidentals", status="closed"), self.db, self.user)
        self.assertEqual(updated["name"], "Incidentals")
        self.assertEqual(updated["status"], "closed")


if __name__ == "__main__":
    unittest.main()
