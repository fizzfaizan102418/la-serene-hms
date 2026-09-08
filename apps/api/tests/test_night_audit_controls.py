import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.models import BusinessDateState, Role, User
from app.night_audit import build_summary, get_business_date


class NightAuditControlTests(unittest.TestCase):
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
        self.user = User(username="admin", password_hash="test", role_id=role.id)
        self.db.add(self.user)
        self.db.flush()
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime.utcnow()))
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_night_audit_uses_controlled_business_date(self):
        self.assertEqual(get_business_date(self.db), date(2026, 9, 8))
        summary = build_summary(self.db, get_business_date(self.db))
        self.assertEqual(summary["business_date"], date(2026, 9, 8))
        self.assertTrue(summary["posting_open"])
        self.assertEqual(summary["finance"]["status"], "balanced")
        self.assertEqual(summary["finance"]["total_debits"], Decimal("0.00"))
        self.assertEqual(summary["finance"]["total_credits"], Decimal("0.00"))

    def test_closed_business_date_is_not_posting_open(self):
        state = self.db.get(BusinessDateState, 1)
        state.last_closed_at = datetime(2026, 9, 8, 23, 59, 0)
        self.db.commit()
        summary = build_summary(self.db, date(2026, 9, 8))
        self.assertFalse(summary["posting_open"])


if __name__ == "__main__":
    unittest.main()
