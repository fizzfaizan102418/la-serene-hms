import unittest
from datetime import date, datetime
from pathlib import Path
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
from app.finance_controls import payment_reconciliation, revenue_report, trial_balance
from app.night_audit import build_json, build_summary
from app.models import BusinessDateState, Role, User


class NightAuditPackTests(unittest.TestCase):
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
        self.db.add(BusinessDateState(id=1, current_business_date=date(2026, 9, 8), opened_at=datetime.utcnow()))
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_financial_pack_contains_trial_balance_payment_and_revenue_sections(self):
        summary = build_summary(self.db, date(2026, 9, 8))
        finance = summary["finance"]
        self.assertIn("trial_balance", finance)
        self.assertIn("payment_reconciliation", finance)
        self.assertIn("revenue_reconciliation", finance)
        self.assertTrue(finance["trial_balance"]["balanced"])
        self.assertEqual(finance["trial_balance"]["total_debit"], 0)
        self.assertEqual(finance["trial_balance"]["total_credit"], 0)
        self.assertEqual(finance["payment_reconciliation"]["net_total"], 0)
        self.assertEqual(finance["revenue_reconciliation"]["difference"], 0)

    def test_closing_json_embeds_finance_controls(self):
        summary = build_summary(self.db, date(2026, 9, 8))
        with tempfile.TemporaryDirectory() as tmp:
            path = build_json(Path(tmp), summary, "no variance", self.user.username, datetime.utcnow())
            text = path.read_text(encoding="utf-8")
            self.assertIn("trial_balance", text)
            self.assertIn("payment_reconciliation", text)
            self.assertIn("revenue_reconciliation", text)

    def test_underlying_finance_reports_match_empty_period(self):
        self.assertTrue(trial_balance(date(2026, 9, 8), self.db, None)["balanced"])
        self.assertEqual(payment_reconciliation(date(2026, 9, 8), self.db, None)["net_total"], 0)
        self.assertEqual(revenue_report(date(2026, 9, 8), self.db, None)["total"], 0)


if __name__ == "__main__":
    unittest.main()
