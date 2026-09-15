import json
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.main import write_audit
from app.models import AuditLog, Role, User


class AuditLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=cls.engine)

    def setUp(self):
        self.db = Session(self.engine)
        self.db.query(AuditLog).delete()
        self.db.query(User).delete()
        self.db.query(Role).delete()
        self.db.commit()

        role = Role(name="admin")
        self.db.add(role)
        self.db.flush()
        self.user = User(
            username="audit-admin",
            password_hash="test-only",
            role_id=role.id,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_audit_entry_persists_action_entity_user_and_json_details(self):
        write_audit(
            self.db,
            "status_change",
            "room",
            42,
            {"from": "dirty", "to": "available"},
            self.user.id,
        )
        self.db.commit()

        entry = self.db.scalar(select(AuditLog).order_by(AuditLog.id.desc()))
        self.assertIsNotNone(entry)
        self.assertEqual(entry.action, "status_change")
        self.assertEqual(entry.entity_type, "room")
        self.assertEqual(entry.entity_id, "42")
        self.assertEqual(entry.user_id, self.user.id)
        self.assertEqual(json.loads(entry.details), {"from": "dirty", "to": "available"})
        self.assertIsNotNone(entry.created_at)

    def test_audit_entry_rolls_back_with_business_transaction(self):
        write_audit(self.db, "create", "guest", 99, {"full_name": "Rollback Test"}, self.user.id)
        self.db.rollback()

        entry = self.db.scalar(select(AuditLog).where(AuditLog.entity_id == "99"))
        self.assertIsNone(entry)


if __name__ == "__main__":
    unittest.main()
