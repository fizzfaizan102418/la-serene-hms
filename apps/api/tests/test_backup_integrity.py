import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from ops.backup.backup import prune_backups, sha256_file, verify_backup


class BackupIntegrityTests(unittest.TestCase):
    def test_checksum_verification_accepts_intact_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "hotel.dump"
            dump.write_bytes(b"authoritative backup payload")
            manifest = root / "hotel.manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "format": "la-serene-hms-backup-v1",
                        "backup_file": dump.name,
                        "sha256": sha256_file(dump),
                        "size_bytes": dump.stat().st_size,
                        "business_date": "2026-09-09",
                        "alembic_revision": "0016_housekeeping_integrity",
                    }
                ),
                encoding="utf-8",
            )
            payload = verify_backup(dump, manifest)
            self.assertEqual(payload["business_date"], "2026-09-09")

    def test_checksum_verification_rejects_tampered_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump = root / "hotel.dump"
            dump.write_bytes(b"original")
            manifest = root / "hotel.manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "backup_file": dump.name,
                        "sha256": sha256_file(dump),
                        "size_bytes": dump.stat().st_size,
                    }
                ),
                encoding="utf-8",
            )
            dump.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify_backup(dump, manifest)

    def test_retention_removes_old_backup_and_manifest_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_dump = root / "old.dump"
            new_dump = root / "new.dump"
            old_dump.write_bytes(b"old")
            new_dump.write_bytes(b"new")
            old_manifest = root / "old.manifest.json"
            new_manifest = root / "new.manifest.json"
            old_manifest.write_text(json.dumps({"backup_file": old_dump.name}), encoding="utf-8")
            new_manifest.write_text(json.dumps({"backup_file": new_dump.name}), encoding="utf-8")
            os.utime(old_manifest, (1, 1))
            os.utime(new_manifest, (2, 2))
            prune_backups(root, retain=1)
            self.assertFalse(old_dump.exists())
            self.assertFalse(old_manifest.exists())
            self.assertTrue(new_dump.exists())
            self.assertTrue(new_manifest.exists())


if __name__ == "__main__":
    unittest.main()
