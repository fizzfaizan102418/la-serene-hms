import unittest

from app.migration_guard import schema_revision_status


class MigrationGuardTests(unittest.TestCase):
    def test_matching_single_revision_is_at_head(self):
        self.assertEqual(schema_revision_status(["abc123"], ["abc123"]), "abc123")

    def test_pending_migration_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "does not match application head"):
            schema_revision_status(["abc123"], ["def456"])

    def test_missing_revision_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one current"):
            schema_revision_status([], ["abc123"])

    def test_multiple_heads_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one Alembic head"):
            schema_revision_status(["abc123"], ["abc123", "def456"])

    def test_multiple_current_revisions_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one current"):
            schema_revision_status(["abc123", "def456"], ["abc123"])


if __name__ == "__main__":
    unittest.main()
