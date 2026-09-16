import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.main import create_guest, list_guests, update_guest
from app.models import AuditLog, Guest, Role, User
from app.schemas import GuestCreate


class GuestProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        role = Role(id=1, name="admin")
        user = User(id=1, username="admin", password_hash="test", role_id=1)
        guest = Guest(id=1, full_name="Test Guest", phone="03000000000", email="test.guest@example.com", address="Old address", id_document="OLD-ID")
        self.db.add_all([role, user, guest])
        self.db.commit()
        self.user = user

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_guest_profile_update_changes_master_record_and_writes_audit(self):
        payload = GuestCreate(
            full_name="Updated Guest",
            phone="03111111111",
            email="updated@example.com",
            address="New address",
            id_document="NEW-ID",
        )
        updated = update_guest(1, payload, self.db, self.user)

        self.assertEqual(updated.full_name, "Updated Guest")
        self.assertEqual(updated.phone, "03111111111")
        self.assertEqual(updated.email, "updated@example.com")
        self.assertEqual(updated.address, "New address")
        self.assertEqual(updated.id_document, "NEW-ID")

        audit = self.db.scalar(
            select(AuditLog).where(
                AuditLog.action == "update",
                AuditLog.entity_type == "guest",
                AuditLog.entity_id == "1",
            ).order_by(AuditLog.id.desc()).limit(1)
        )
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user_id, 1)


    def test_guest_search_is_limited_at_query_level(self):
        self.db.add_all([
            Guest(full_name=f"Search Guest {i:02d}", phone=f"03000000{i:03d}")
            for i in range(25)
        ])
        self.db.commit()

        results = list_guests("Search", 20, self.db, self.user)
        self.assertEqual(len(results), 20)

    def test_guest_create_rejects_duplicate_identity(self):
        payload = GuestCreate(full_name="Duplicate Guest", phone="03000000000", id_document="NEW-ID")
        with self.assertRaises(Exception) as context:
            create_guest(payload, self.db, self.user)
        self.assertEqual(getattr(context.exception, "status_code", None), 409)
        detail = getattr(context.exception, "detail", {})
        self.assertIn("Possible duplicate guest record", detail.get("message", ""))
        self.assertEqual(detail.get("matches", [])[0]["id"], 1)

    def test_guest_update_rejects_duplicate_identity(self):
        self.db.add(Guest(id=2, full_name="Second Guest", phone="03222222222", id_document="SECOND-ID"))
        self.db.commit()
        payload = GuestCreate(full_name="Updated Guest", phone="03222222222", id_document="NEW-ID")
        with self.assertRaises(Exception) as context:
            update_guest(1, payload, self.db, self.user)
        self.assertEqual(getattr(context.exception, "status_code", None), 409)

    def test_guest_profile_update_rejects_unknown_guest(self):
        payload = GuestCreate(full_name="Updated Guest")
        with self.assertRaises(Exception) as context:
            update_guest(999, payload, self.db, self.user)
        self.assertEqual(getattr(context.exception, "status_code", None), 404)


if __name__ == "__main__":
    unittest.main()
