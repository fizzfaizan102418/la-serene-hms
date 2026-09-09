from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .backup import create_backup, verify_backup


def configure_logging(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=output_dir / "backup.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and verify a scheduled HMS PostgreSQL backup")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--retain", type=int, default=7)
    args = parser.parse_args()
    configure_logging(args.output_dir)

    try:
        dump_path, manifest_path = create_backup(args.output_dir, retain=args.retain)
        manifest = verify_backup(dump_path, manifest_path)
        result = {
            "status": "verified",
            "backup_file": dump_path.name,
            "manifest_file": manifest_path.name,
            "sha256": manifest["sha256"],
            "business_date": manifest["business_date"],
            "alembic_revision": manifest["alembic_revision"],
        }
        logging.info("Backup completed and checksum verified: %s", dump_path.name)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        # Do not log the exception text: database-driver errors can contain
        # connection details, and the production backup log must never become
        # a credential or connection-string disclosure channel.
        logging.error("Scheduled backup failed: %s", type(exc).__name__)
        print(json.dumps({"status": "failed", "error": "Scheduled backup failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
