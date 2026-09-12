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
            root_logger = logging.getLogger()
            test_handler = next(
                handler
                for handler in root_logger.handlers
                if isinstance(handler, logging.handlers.RotatingFileHandler)
                and Path(handler.baseFilename) == log_file
            )

            try:
                logger = logging.getLogger("operations-test")
                logger.info("K9 logging smoke test")
                test_handler.flush()

                self.assertEqual(log_file, log_dir / "api.log")
                self.assertIn(
                    "K9 logging smoke test",
                    log_file.read_text(encoding="utf-8"),
                )
            finally:
                root_logger.removeHandler(test_handler)
                test_handler.close()


if __name__ == "__main__":
    unittest.main()
