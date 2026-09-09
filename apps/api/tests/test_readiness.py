import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.readiness import readiness


class ReadinessTests(unittest.TestCase):
    def test_healthy_database_returns_ready(self):
        with patch("app.readiness.check_database_ready") as check:
            response = readiness()

        check.assert_called_once_with()
        self.assertEqual(response.status, "ready")
        self.assertEqual(response.service, "la-serene-hms-api")
        self.assertEqual(response.mode, "postgresql")

    def test_database_outage_returns_generic_503(self):
        with patch(
            "app.readiness.check_database_ready",
            side_effect=RuntimeError("postgresql://secret-user:secret-password@db/hms"),
        ):
            with self.assertRaises(HTTPException) as raised:
                readiness()

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Database readiness check failed")
        self.assertNotIn("secret-password", str(raised.exception.detail))
        self.assertNotIn("postgresql://", str(raised.exception.detail))

    def test_database_recovery_returns_ready_after_outage(self):
        with patch(
            "app.readiness.check_database_ready",
            side_effect=[RuntimeError("database unavailable"), None],
        ) as check:
            with self.assertRaises(HTTPException) as raised:
                readiness()
            self.assertEqual(raised.exception.status_code, 503)

            response = readiness()

        self.assertEqual(check.call_count, 2)
        self.assertEqual(response.status, "ready")
        self.assertEqual(response.service, "la-serene-hms-api")
        self.assertEqual(response.mode, "postgresql")


if __name__ == "__main__":
    unittest.main()
