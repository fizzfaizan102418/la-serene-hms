import unittest

from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
import app.models  # noqa: F401
from app.ledger import current_business_date
from app.models import BusinessDateState


class LedgerBusinessDateAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_missing_business_date_does_not_use_os_date_or_create_state(self):
        with Session(self.engine) as db:
            with self.assertRaises(HTTPException) as context:
                current_business_date(db)
            self.assertEqual(context.exception.status_code, 503)
            self.assertIsNone(db.scalar(select(BusinessDateState).limit(1)))

    def test_ledger_reads_persisted_business_date(self):
        with Session(self.engine) as db:
            expected = date(2026, 9, 12)
            db.add(BusinessDateState(id=1, current_business_date=expected))
            db.commit()
            self.assertEqual(current_business_date(db), expected)


if __name__ == "__main__":
    unittest.main()
