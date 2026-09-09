import json
import tempfile
import unittest
from pathlib import Path

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
            for name in ("old", "new"):
                dump = root / f"{name}.dump"
                dump.write_bytes(name.encode())
                (root / f"{name}.manifest.json").write_text(
                    json.dumps({"backup_file": dump.name}), encoding="utf-8"
                )
            old_manifest = root / "old.manifest.json"
            new_manifest = root / "new.manifest.json"
            old_manifest.touch()
            new_manifest.touch()
            old_manifest.write_text(json.dumps({"backup_file": "old.dump"}), encoding="utf-8")
            new_manifest.write_text(json.dumps({"backup_file": "new.dump"}), encoding="utf-8")
            old_manifest.utime = None
            import os
            os.utime(old_manifest, (1, 1))
            os.utime(new_manifest, (2, 2))
            prune_backups(root, retain=1)
            self.assertFalse((root / "old.dump").exists())
            self.assertFalse(old_manifest.exists())
            self.assertTrue((root / "new.dump").exists())
            self.assertTrue(new_manifest.exists())


if __name__ == "__main__":
    unittest.main()
