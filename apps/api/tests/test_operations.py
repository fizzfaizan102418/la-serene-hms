import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.health import readiness
from app.logging_config import configure_production_logging


class OperationsTest(unittest.TestCase):
    def test_readiness_returns_ready_when_database_query_succeeds(self):
        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, statement):
                self.statement = statement

        with patch("app.health.Session", return_value=FakeSession()):
            result = readiness()

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.service, "la-serene-hms-api")

    def test_readiness_returns_503_when_database_query_fails(self):
        with patch("app.health.Session", side_effect=OSError("database offline")):
            with self.assertRaises(HTTPException) as raised:
                readiness()
        self.assertEqual(raised.exception.status_code, 503)

    def test_production_logging_writes_to_rotating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            log_file = configure_production_logging(log_dir)
            logger = logging.getLogger("operations-test")
            logger.info("K9 logging smoke test")
            for handler in logging.getLogger().handlers:
                handler.flush()

            self.assertEqual(log_file, log_dir / "api.log")
            self.assertIn("K9 logging smoke test", log_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
