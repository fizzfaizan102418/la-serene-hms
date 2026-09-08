import unittest
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.business_date import get_current_business_date
from app.db import Base
import app.models  # noqa: F401
from app.models import BusinessDateState


class BusinessDateArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_business_date_reads_persisted_state(self):
        with Session(self.engine) as db:
            expected = date.today() + timedelta(days=7)
            db.add(BusinessDateState(id=1, current_business_date=expected))
            db.commit()
            self.assertEqual(get_current_business_date(db), expected)

    def test_missing_state_is_an_error_without_fallback(self):
        with Session(self.engine) as db:
            with self.assertRaises(HTTPException) as context:
                get_current_business_date(db)
            self.assertEqual(context.exception.status_code, 503)

    def test_fallback_is_explicit_for_legacy_compatibility(self):
        with Session(self.engine) as db:
            self.assertEqual(get_current_business_date(db, fallback_to_today=True), date.today())


if __name__ == "__main__":
    unittest.main()
