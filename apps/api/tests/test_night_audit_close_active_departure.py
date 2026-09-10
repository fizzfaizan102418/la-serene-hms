import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
import app.financial_models  # noqa: F401
import app.models  # noqa: F401
import app.pms_core  # noqa: F401
import app.stay_lifecycle  # noqa: F401
from app.models import Reservation
from app.night_audit import close_day


class NightAuditCloseActiveDepartureTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)
        self.business_date = date(2026, 9, 11)
        self.state = SimpleNamespace(
            current_business_date=self.business_date,
            last_closed_at=None,
            opened_at=datetime(2026, 9, 10, 5, 0, 0),
        )
        self.user = SimpleNamespace(id=1, username="admin")

    def tearDown(self):
        self.db.close()

    def add_active_reservation(self, check_out: date):
        reservation = Reservation(
            guest_id=None,
            check_in=date(2026, 9, 10),
            check_out=check_out,
            status="checked_in",
        )
        self.db.add(reservation)
        self.db.commit()
        return reservation

    def assert_close_rejected(self, reservation):
        with patch("app.night_audit.lock_current_business_date", return_value=self.state), patch(
            "app.night_audit.finance_snapshot"
        ) as finance_snapshot, patch("app.night_audit.create_pack") as create_pack:
            with self.assertRaises(HTTPException) as context:
                close_day(None, self.db, self.user)

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(
            context.exception.detail,
            "Active departures must be checked out before Night Audit can close the business date",
        )
        finance_snapshot.assert_not_called()
        create_pack.assert_not_called()
        self.db.refresh(reservation)
        self.assertEqual(reservation.status, "checked_in")
        self.assertEqual(self.state.current_business_date, self.business_date)
        self.assertIsNone(self.state.last_closed_at)

    def test_close_day_rejects_active_departure_without_mutation(self):
        reservation = self.add_active_reservation(self.business_date)
        self.assert_close_rejected(reservation)

    def test_close_day_rejects_overdue_active_departure_without_mutation(self):
        reservation = self.add_active_reservation(date(2026, 9, 10))
        self.assert_close_rejected(reservation)


if __name__ == "__main__":
    unittest.main()
