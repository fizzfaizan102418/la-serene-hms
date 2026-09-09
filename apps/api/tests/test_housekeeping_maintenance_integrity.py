import unittest
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
import app.housekeeping_control  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.front_desk import atomic_checkout
from app.housekeeping import mark_room_clean, mark_room_out_of_order, release_room_from_out_of_order
from app.housekeeping_control import (
    MaintenanceCreate,
    HousekeepingTaskCreate,
    active_housekeeping_task,
    create_maintenance_block,
    create_task,
    housekeeping_tasks,
    maintenance_blocks,
    resolve_maintenance_block,
    start_task,
    complete_task,
)
from app.models import BusinessDateState, Folio, Guest, Reservation, ReservationRoom, Role, Room, RoomType, User
from app.pms_core import Stay


class HousekeepingMaintenanceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.today = date(2026, 9, 9)
        self.db.add(BusinessDateState(id=1, current_business_date=self.today))
        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(username="hk-admin", password_hash="test", role_id=role.id)
        self.db.add(self.user)
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add(room_type)
        self.db.flush()
        self.room = Room(number="801", room_type_id=room_type.id, status="dirty")
        self.room2 = Room(number="802", room_type_id=room_type.id, status="available")
        self.room3 = Room(number="803", room_type_id=room_type.id, status="occupied")
        self.db.add_all([self.room, self.room2, self.room3])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.engine.dispose()

    def test_housekeeping_task_lifecycle_moves_dirty_room_to_available_atomically(self):
        task = create_task(self.room.id, HousekeepingTaskCreate(task_type="manual_clean", reason="Deep clean", priority="high"), self.db, self.user)
        self.assertEqual(task["status"], "pending")
        started = start_task(task["id"], self.db, self.user)
        self.assertEqual(started["status"], "in_progress")
        completed = complete_task(task["id"], self.db, self.user)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.db.get(Room, self.room.id).status, "available")
        stored = self.db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.id == task["id"])).mappings().one()
        self.assertEqual(stored["status"], "completed")

    def test_legacy_clean_route_is_single_transaction(self):
        result = mark_room_clean(self.room.id, self.db, self.user)
        self.assertEqual(result["status"], "available")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(housekeeping_tasks)), 1)
        task = self.db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.room_id == self.room.id)).mappings().one()
        self.assertEqual(task["status"], "completed")

    def test_active_maintenance_blocks_cleaning_and_room_availability(self):
        block = create_maintenance_block(self.room2.id, MaintenanceCreate(reason="Broken AC", severity="high"), self.db, self.user)
        self.assertEqual(self.db.get(Room, self.room2.id).status, "out_of_order")
        with self.assertRaises(HTTPException) as ctx:
            create_task(self.room2.id, HousekeepingTaskCreate(reason="Should fail"), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        resolved = resolve_maintenance_block(block["id"], self.db, self.user)
        self.assertEqual(resolved["room_status"], "dirty")
        self.assertIsNotNone(active_housekeeping_task(self.db, self.room2.id))

    def test_maintenance_cannot_be_created_for_occupied_or_reserved_room(self):
        with self.assertRaises(HTTPException) as ctx:
            create_maintenance_block(self.room3.id, MaintenanceCreate(reason="Occupied", severity="normal"), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        guest = Guest(full_name="Reserved Guest")
        self.db.add(guest)
        self.db.flush()
        reservation = Reservation(guest_id=guest.id, check_in=self.today, check_out=self.today + timedelta(days=1), status="reserved")
        self.db.add(reservation)
        self.db.flush()
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=self.room2.id))
        self.db.commit()
        self.db.refresh(self.room2)
        with self.assertRaises(HTTPException) as ctx:
            create_maintenance_block(self.room2.id, MaintenanceCreate(reason="Reserved", severity="normal"), self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_resolve_requires_out_of_order_state(self):
        block = create_maintenance_block(self.room2.id, MaintenanceCreate(reason="Plumbing", severity="critical"), self.db, self.user)
        self.db.get(Room, self.room2.id).status = "available"
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            resolve_maintenance_block(block["id"], self.db, self.user)
        self.assertEqual(ctx.exception.status_code, 409)
        self.db.rollback()

    def test_dirty_checkout_creates_pending_housekeeping_work(self):
        guest = Guest(full_name="Checkout Guest")
        self.db.add(guest)
        self.db.flush()
        reservation = Reservation(guest_id=guest.id, check_in=self.today, check_out=self.today + timedelta(days=1), status="checked_in")
        self.db.add(reservation)
        self.db.flush()
        folio = Folio(reservation_id=reservation.id, status="open")
        self.db.add(folio)
        self.db.add(ReservationRoom(reservation_id=reservation.id, room_id=self.room3.id))
        stay = Stay(reservation_id=reservation.id, room_id=self.room3.id, guest_id=guest.id, status="checked_in", check_in=self.today, check_out=self.today + timedelta(days=1), agreed_rate=Decimal("0"))
        self.db.add(stay)
        self.db.commit()
        self.assertEqual(self.db.get(Room, self.room3.id).status, "occupied")
        atomic_checkout(reservation.id, self.db, self.user)
        self.assertEqual(self.db.get(Room, self.room3.id).status, "dirty")
        task = self.db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.room_id == self.room3.id)).mappings().first()
        self.assertIsNotNone(task)
        self.assertEqual(task["task_type"], "checkout_clean")
        self.assertEqual(task["status"], "pending")

    def test_business_date_is_persisted_for_housekeeping_and_maintenance(self):
        self.db.get(BusinessDateState, 1).current_business_date = self.today + timedelta(days=2)
        self.db.commit()
        task = create_task(self.room.id, HousekeepingTaskCreate(reason="Date test"), self.db, self.user)
        self.assertEqual(task["business_date"], self.today + timedelta(days=2))
        self.assertEqual(mark_room_clean(self.room.id, self.db, self.user)["business_date"], self.today + timedelta(days=2))

    def test_same_room_cannot_have_two_active_housekeeping_tasks(self):
        first = create_task(self.room.id, HousekeepingTaskCreate(reason="First"), self.db, self.user)
        second = create_task(self.room.id, HousekeepingTaskCreate(reason="Second"), self.db, self.user)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(housekeeping_tasks)), 1)

    def test_out_of_order_compatibility_routes_use_maintenance_blocks(self):
        result = mark_room_out_of_order(self.room2.id, self.db, self.user)
        self.assertEqual(result["status"], "out_of_order")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(maintenance_blocks)), 1)
        released = release_room_from_out_of_order(self.room2.id, self.db, self.user)
        self.assertEqual(released["status"], "dirty")
        self.assertIsNotNone(released["housekeeping_task_id"])


if __name__ == "__main__":
    unittest.main()
