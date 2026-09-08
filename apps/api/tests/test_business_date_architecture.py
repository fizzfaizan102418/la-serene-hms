import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.models  # noqa: F401
from app.business_date import get_current_business_date
from app.models import BusinessDateState


class BusinessDateArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_business_date_reads_state_not_system_date(self):
        with Session(self.engine) as db:
            expected = date.today() + timedelta(days=7)
            db.add(BusinessDateState(id=1, current_business_date=expected))
            db.commit()
            self.assertEqual(get_current_business_date(db), expected)

    def test_missing_state_requires_initialization_unless_fallback_requested(self):
        with Session(self.engine) as db:
            with self.assertRaises(Exception):
                get_current_business_date(db)
            self.assertEqual(get_current_business_date(db, fallback_to_today=True), date.today())


if __name__ == "__main__":
    unittest.main()
