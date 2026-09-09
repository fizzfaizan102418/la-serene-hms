from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .backup import restore_and_verify


def normalize_target_name(url: str) -> tuple[str, int | None, str]:
    parsed = urlsplit(url)
    database = parsed.path.lstrip("/")
    return parsed.hostname or "", parsed.port, database


def require_isolated_recovery_target(source_url: str, target_url: str) -> None:
    source_host, source_port, source_db = normalize_target_name(source_url)
    target_host, target_port, target_db = normalize_target_name(target_url)
    if not target_db:
        raise RuntimeError("Recovery target database name is required")
    if target_db == source_db and target_host == source_host and target_port == source_port:
        raise RuntimeError("Refusing recovery into the live production database")
    if not (target_db.endswith("_recovery") or target_db.endswith("_recovery_test")):
        raise RuntimeError("Recovery target database name must end with _recovery or _recovery_test")


def write_report(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely restore and verify a La Serene HMS backup into an isolated recovery database"
    )
    parser.add_argument("backup_file", type=Path)
    parser.add_argument("manifest_file", type=Path)
    parser.add_argument("--target-database-url", required=True)
    parser.add_argument("--source-database-url", default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Explicitly confirm that the target is an isolated recovery database",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.confirm:
        raise RuntimeError("Refusing restore: pass --confirm after validating the recovery target")

    source_url = args.source_database_url
    if not source_url:
        import os

        source_url = os.environ.get("HMS_DATABASE_URL", "")
    if not source_url:
        raise RuntimeError("Source database URL is required via HMS_DATABASE_URL or --source-database-url")

    require_isolated_recovery_target(source_url, args.target_database_url)
    result = restore_and_verify(args.backup_file, args.manifest_file, args.target_database_url)
    report = {
        "status": result["status"],
        "backup_file": result["backup_file"],
        "business_date": result["business_date"],
        "alembic_revision": result["alembic_revision"],
        "target_database": normalize_target_name(args.target_database_url)[2],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if args.report:
        write_report(args.report, report)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
