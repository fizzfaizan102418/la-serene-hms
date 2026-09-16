import unittest
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.expenses import create_expense, expense_summary, list_expenses
from app.models import AuditLog, BusinessDateState, Role, User


class ExpensesModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        role = Role(id=1, name="admin")
        user = User(id=1, username="admin", password_hash="test", role_id=1)
        self.business_date = date(2026, 9, 16)
        state = BusinessDateState(id=1, current_business_date=self.business_date, opened_at=datetime.utcnow())
        self.db.add_all([role, user, state]); self.db.commit(); self.user = user

    def tearDown(self):
        self.db.rollback(); self.db.close()

    def test_create_expense_and_summary(self):
        payload = type("Payload", (), {
            "expense_date": self.business_date, "category": "Electricity",
            "description": "Monthly electricity bill", "amount": Decimal("75000.00"),
            "payment_method": "Bank", "paid_to": "WAPDA", "reference": "ELEC-SEP",
            "department": "Administration", "notes": "Main hotel meter",
        })()
        created = create_expense(payload, self.db, self.user)
        self.assertEqual(created["expense_no"], "EXP-000001")
        self.assertEqual(created["category"], "Electricity")
        self.assertEqual(created["amount"], Decimal("75000.00"))
        self.assertEqual(created["payment_method"], "Bank")

        rows = list_expenses(from_date=self.business_date, to_date=self.business_date, db=self.db, _=self.user)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["paid_to"], "WAPDA")

        summary = expense_summary(from_date=self.business_date, to_date=self.business_date, db=self.db, _=self.user)
        self.assertEqual(summary["total"], Decimal("75000.00"))
        self.assertEqual(summary["by_category"][0]["category"], "Electricity")
        self.assertEqual(summary["by_department"][0]["department"], "Administration")
        self.assertEqual(summary["by_payment_method"][0]["payment_method"], "Bank")

        audit = self.db.scalar(select(AuditLog).where(AuditLog.entity_type == "expense").order_by(AuditLog.id.desc()).limit(1))
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user_id, 1)


if __name__ == "__main__":
    unittest.main()
