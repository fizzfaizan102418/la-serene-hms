import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomType, StayOccupant, User
from app.pms_core import Stay
from app.stay_lifecycle import (
    FolioWindowCreate,
    OccupantUpdate,
    RoomReplacement,
    StayDatesUpdate,
    StayFolioWindow,
    cancel_stay_room,
    create_stay_folio_window,
    remove_stay_occupant,
    replace_stay_room,
    update_stay_dates,
    update_stay_occupant,
)


class StayLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=cls.engine)

    def setUp(self):
        self.db = Session(self.engine)
        role = Role(name="reception")
        self.db.add(role)
        guest = Guest(full_name="Booking Guest")
        occupant = Guest(full_name="Room Occupant")
        self.db.add_all([guest, occupant])
        self.db.flush()
        user = User(username="reception", password_hash="test", role_id=role.id)
        self.db.add(user)
        room_type = RoomType(name=f"Standard-{guest.id}", base_rate=100)
        self.db.add(room_type)
        self.db.flush()
        room_a = Room(number=f"A-{guest.id}", room_type_id=room_type.id, status="reserved")
        room_b = Room(number=f"B-{guest.id}", room_type_id=room_type.id, status="available")
        self.db.add_all([room_a, room_b])
        self.db.flush()
        reservation = Reservation(guest_id=guest.id, check_in=date(2026, 9, 8), check_out=date(2026, 9, 12), status="reserved")
        self.db.add(reservation)
        self.db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=room_a.id))
        stay = Stay(reservation_id=reservation.id, room_id=room_a.id, guest_id=guest.id, status="reserved", check_in=date(2026, 9, 8), check_out=date(2026, 9, 12), agreed_rate=Decimal("100.00"))
        self.db.add(stay)
        self.db.flush()
        self.db.add(StayOccupant(stay_id=stay.id, guest_id=guest.id, role="primary", is_primary=True, check_in=stay.check_in, check_out=stay.check_out))
        self.db.add(StayOccupant(stay_id=stay.id, guest_id=occupant.id, role="occupant", is_primary=False, check_in=stay.check_in, check_out=stay.check_out))
        self.db.commit()
        self.stay = self.db.get(Stay, stay.id)
        self.user = user
        self.guest = guest
        self.occupant = occupant
        self.room_a = room_a
        self.room_b = room_b
        self.reservation = reservation

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_room_replacement_updates_stay_and_reservation_link(self):
        result = replace_stay_room(self.stay.id, RoomReplacement(to_room_id=self.room_b.id, reason="Guest requested alternate room"), self.db, self.user)
        self.assertEqual(result["to_room_id"], self.room_b.id)
        self.assertEqual(self.db.get(Stay, self.stay.id).room_id, self.room_b.id)
        self.assertEqual(self.db.get(Room, self.room_a.id).status, "available")
        links = self.db.scalars(select(ReservationRoom).where(ReservationRoom.reservation_id == self.reservation.id)).all()
        self.assertEqual([link.room_id for link in links], [self.room_b.id])

    def test_room_stay_dates_can_be_changed_independently(self):
        result = update_stay_dates(self.stay.id, StayDatesUpdate(check_in=date(2026, 9, 9), check_out=date(2026, 9, 11)), self.db, self.user)
        self.assertEqual(result["check_in"], date(2026, 9, 9))
        self.assertEqual(result["check_out"], date(2026, 9, 11))
        occupants = self.db.scalars(select(StayOccupant).where(StayOccupant.stay_id == self.stay.id)).all()
        self.assertTrue(all(item.check_in == date(2026, 9, 9) and item.check_out == date(2026, 9, 11) for item in occupants))

    def test_occupant_can_be_promoted_and_removed(self):
        occupants = self.db.scalars(select(StayOccupant).where(StayOccupant.stay_id == self.stay.id).order_by(StayOccupant.id)).all()
        result = update_stay_occupant(self.stay.id, occupants[1].id, OccupantUpdate(is_primary=True, role="primary"), self.db, self.user)
        self.assertTrue(result["is_primary"])
        self.db.expire_all()
        current = self.db.scalars(select(StayOccupant).where(StayOccupant.stay_id == self.stay.id).order_by(StayOccupant.id)).all()
        self.assertEqual(sum(1 for item in current if item.is_primary), 1)
        remove_stay_occupant(self.stay.id, current[1].id, self.db, self.user)
        self.assertEqual(len(self.db.scalars(select(StayOccupant).where(StayOccupant.stay_id == self.stay.id)).all()), 1)

    def test_reserved_room_stay_can_be_cancelled_without_erasing_history(self):
        result = cancel_stay_room(self.stay.id, None, self.db, self.user)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.db.get(Room, self.room_a.id).status, "available")
        self.assertEqual(self.db.scalars(select(ReservationRoom).where(ReservationRoom.reservation_id == self.reservation.id)).all(), [])

    def test_room_level_folio_window_is_routable(self):
        result = create_stay_folio_window(self.stay.id, FolioWindowCreate(name="Room Charges", payer_type="guest", guest_id=self.guest.id), self.db, self.user)
        self.assertEqual(result["stay_id"], self.stay.id)
        row = self.db.get(StayFolioWindow, result["id"])
        self.assertEqual(row.folio_id, 1)
        self.assertEqual(row.payer_type, "guest")


if __name__ == "__main__":
    unittest.main()
