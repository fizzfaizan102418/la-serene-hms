import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.expenses import create_expense, expense_summary, list_expenses
from app.models import AuditLog, Role, User


class ExpensesModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN expense_date DATE"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN expense_no VARCHAR(40)"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN category VARCHAR(80)"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN paid_to VARCHAR(160)"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN reference VARCHAR(100)"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN department VARCHAR(60)"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN notes TEXT"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN created_by INTEGER"))
        self.db.execute(text("ALTER TABLE expenses ADD COLUMN status VARCHAR(20)"))
        self.db.commit()
        role = Role(id=1, name="admin")
        user = User(id=1, username="admin", password_hash="test", role_id=1)
        self.db.add_all([role, user]); self.db.commit(); self.user = user

    def tearDown(self):
        self.db.rollback(); self.db.close()

    def test_create_expense_and_summary(self):
        payload = type("Payload", (), {
            "expense_date": date(2026, 9, 13),
            "category": "Electricity",
            "description": "Monthly electricity bill",
            "amount": Decimal("75000.00"),
            "payment_method": "Bank",
            "paid_to": "WAPDA",
            "reference": "ELEC-SEP",
            "department": "Administration",
            "notes": "Main hotel meter",
        })()
        created = create_expense(payload, self.db, self.user)
        self.assertEqual(created["expense_no"], "EXP-000001")
        self.assertEqual(created["category"], "Electricity")
        self.assertEqual(created["amount"], Decimal("75000.00"))
        self.assertEqual(created["payment_method"], "Bank")

        rows = list_expenses(from_date=date(2026, 9, 13), to_date=date(2026, 9, 13), db=self.db, _=self.user)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["paid_to"], "WAPDA")

        summary = expense_summary(from_date=date(2026, 9, 13), to_date=date(2026, 9, 13), db=self.db, _=self.user)
        self.assertEqual(summary["total"], Decimal("75000.00"))
        self.assertEqual(summary["by_category"][0]["category"], "Electricity")
        self.assertEqual(summary["by_department"][0]["department"], "Administration")
        self.assertEqual(summary["by_payment_method"][0]["payment_method"], "Bank")

        audit = self.db.scalar(select(AuditLog).where(AuditLog.entity_type == "expense").order_by(AuditLog.id.desc()).limit(1))
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user_id, 1)


if __name__ == "__main__":
    unittest.main()
