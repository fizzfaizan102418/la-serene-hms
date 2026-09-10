import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
import app.housekeeping_control  # noqa: F401
import app.models  # noqa: F401
from app.housekeeping_control import HousekeepingTaskCreate, create_task, housekeeping_tasks
from app.models import BusinessDateState, Role, Room, RoomType, User


class ManualCleanAvailableRoomTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        today = date(2026, 9, 10)
        self.db.add(BusinessDateState(id=1, current_business_date=today))
        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(username="hk-admin", password_hash="test", role_id=role.id)
        room_type = RoomType(name="Standard", base_rate=100)
        self.db.add_all([self.user, room_type])
        self.db.flush()
        self.dirty_room = Room(number="901", room_type_id=room_type.id, status="dirty")
        self.available_room = Room(number="902", room_type_id=room_type.id, status="available")
        self.db.add_all([self.dirty_room, self.available_room])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.engine.dispose()

    def test_manual_clean_is_rejected_for_available_room(self):
        with self.assertRaises(HTTPException) as ctx:
            create_task(
                self.available_room.id,
                HousekeepingTaskCreate(task_type="manual_clean", reason="Manual clean"),
                self.db,
                self.user,
            )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "Manual cleaning tasks can only be created for dirty rooms")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(housekeeping_tasks)), 0)

    def test_manual_clean_still_starts_on_dirty_room(self):
        task = create_task(
            self.dirty_room.id,
            HousekeepingTaskCreate(task_type="manual_clean", reason="Manual clean"),
            self.db,
            self.user,
        )
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["room_id"], self.dirty_room.id)


if __name__ == "__main__":
    unittest.main()
