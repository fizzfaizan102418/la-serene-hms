import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from ops.backup import recover


class RecoveryRunnerTests(unittest.TestCase):
    def test_rejects_production_database_as_target(self):
        source = "postgresql://hms:secret@127.0.0.1:5432/la_serene_hms"
        with self.assertRaisesRegex(RuntimeError, "live production"):
            recover.require_isolated_recovery_target(
                source,
                "postgresql://hms:secret@127.0.0.1:5432/la_serene_hms",
            )

    def test_rejects_non_recovery_target_name(self):
        with self.assertRaisesRegex(RuntimeError, "must end with"):
            recover.require_isolated_recovery_target(
                "postgresql://hms:secret@127.0.0.1:5432/la_serene_hms",
                "postgresql://hms:secret@127.0.0.1:5432/test_database",
            )

    def test_accepts_isolated_recovery_target(self):
        recover.require_isolated_recovery_target(
            "postgresql://hms:secret@127.0.0.1:5432/la_serene_hms",
            "postgresql://hms:secret@127.0.0.1:5432/la_serene_hms_recovery",
        )

    def test_report_contains_no_database_credentials(self):
        with TemporaryDirectory() as temp:
            report = Path(temp) / "recovery.json"
            result = {
                "status": "verified",
                "backup_file": "backup.dump",
                "business_date": "2026-09-09",
                "alembic_revision": "head",
                "target_database": "la_serene_hms_recovery",
                "completed_at": "2026-09-09T00:00:00+00:00",
            }
            recover.write_report(report, result)
            text = report.read_text(encoding="utf-8")
            self.assertIn("la_serene_hms_recovery", text)
            self.assertNotIn("secret", text)
            self.assertNotIn("postgresql://", text)

    @patch("ops.backup.recover.restore_and_verify")
    def test_main_requires_explicit_confirmation(self, restore_mock):
        with patch("sys.argv", ["recover", "backup.dump", "backup.manifest.json", "--target-database-url", "postgresql://hms:x@127.0.0.1:5432/la_serene_hms_recovery"]):
            with self.assertRaisesRegex(RuntimeError, "--confirm"):
                recover.main()
        restore_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
