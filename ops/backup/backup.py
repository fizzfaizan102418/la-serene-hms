from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_postgres_url() -> str:
    url = os.environ.get("HMS_DATABASE_URL", "").strip()
    if not url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        raise RuntimeError("HMS_DATABASE_URL must point to PostgreSQL for production backup operations")
    return url


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_metadata(database_url: str) -> dict[str, object]:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user")
            database_name, database_user = cur.fetchone()
            cur.execute("SELECT current_business_date FROM business_date_state WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("BusinessDateState singleton row is missing; refusing backup")
            business_date = row[0]
            cur.execute("SELECT version_num FROM alembic_version")
            versions = [item[0] for item in cur.fetchall()]
            if len(versions) != 1:
                raise RuntimeError("Expected exactly one Alembic head revision in the live database")
    return {
        "database": database_name,
        "database_user": database_user,
        "business_date": business_date.isoformat() if isinstance(business_date, date) else str(business_date),
        "alembic_revision": versions[0],
    }


def run_pg_dump(database_url: str, destination: Path) -> None:
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--dbname",
        database_url,
        "--file",
        str(destination),
    ]
    subprocess.run(command, check=True)


def write_manifest(manifest_path: Path, payload: dict[str, object]) -> None:
    temp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(manifest_path)


def prune_backups(backup_dir: Path, retain: int) -> None:
    if retain < 1:
        raise ValueError("--retain must be at least 1")
    manifests = sorted(backup_dir.glob("*.manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest in manifests[retain:]:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            dump_name = payload.get("backup_file")
            if isinstance(dump_name, str):
                (backup_dir / dump_name).unlink(missing_ok=True)
        finally:
            manifest.unlink(missing_ok=True)


def create_backup(output_dir: Path, retain: int = 7) -> tuple[Path, Path]:
    database_url = require_postgres_url()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = database_metadata(database_url)
    started = utc_now()
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    dump_name = f"la_serene_hms_{stamp}.dump"
    manifest_name = f"la_serene_hms_{stamp}.manifest.json"

    with tempfile.TemporaryDirectory(dir=output_dir) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_dump = temp_dir / dump_name
        run_pg_dump(database_url, temp_dump)
        digest = sha256_file(temp_dump)
        final_dump = output_dir / dump_name
        temp_dump.replace(final_dump)

    completed = utc_now()
    manifest = {
        "format": "la-serene-hms-backup-v1",
        "backup_file": dump_name,
        "sha256": digest,
        "size_bytes": final_dump.stat().st_size,
        "created_at": completed.isoformat(),
        **metadata,
    }
    write_manifest(output_dir / manifest_name, manifest)
    prune_backups(output_dir, retain)
    return final_dump, output_dir / manifest_name


def verify_backup(backup_file: Path, manifest_file: Path) -> dict[str, object]:
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    expected_file = payload.get("backup_file")
    expected_hash = payload.get("sha256")
    if expected_file != backup_file.name:
        raise RuntimeError("Manifest does not identify the supplied backup file")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise RuntimeError("Manifest SHA-256 is missing or malformed")
    actual_hash = sha256_file(backup_file)
    if actual_hash != expected_hash:
        raise RuntimeError("Backup checksum mismatch; refusing restore verification")
    listed_size = payload.get("size_bytes")
    if listed_size != backup_file.stat().st_size:
        raise RuntimeError("Backup size differs from the manifest")
    return payload


def restore_and_verify(backup_file: Path, manifest_file: Path, target_database_url: str) -> dict[str, object]:
    payload = verify_backup(backup_file, manifest_file)
    if not target_database_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        raise RuntimeError("Target database must be PostgreSQL")

    with tempfile.TemporaryDirectory(prefix="la-serene-hms-restore-") as temp_dir_name:
        list_file = Path(temp_dir_name) / "restore.list"
        subprocess.run(["pg_restore", "--list", str(backup_file)], check=True, stdout=list_file.open("w", encoding="utf-8"))
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--dbname",
                target_database_url,
                str(backup_file),
            ],
            check=True,
        )

    with psycopg.connect(target_database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM business_date_state WHERE id = 1")
            if cur.fetchone()[0] != 1:
                raise RuntimeError("Restored database is missing the BusinessDateState singleton")
            cur.execute("SELECT current_business_date FROM business_date_state WHERE id = 1")
            restored_business_date = cur.fetchone()[0].isoformat()
            if restored_business_date != payload["business_date"]:
                raise RuntimeError("Restored business date does not match backup manifest")
            cur.execute("SELECT version_num FROM alembic_version")
            restored_revision = cur.fetchone()[0]
            if restored_revision != payload["alembic_revision"]:
                raise RuntimeError("Restored Alembic revision does not match backup manifest")
            cur.execute(
                """
                SELECT COUNT(*)
                FROM financial_transactions ft
                LEFT JOIN ledger_entries le ON le.transaction_id = ft.id
                WHERE ft.status = 'posted'
                GROUP BY ft.id
                HAVING COALESCE(SUM(CASE WHEN le.direction = 'debit' THEN le.amount ELSE 0 END), 0)
                    <> COALESCE(SUM(CASE WHEN le.direction = 'credit' THEN le.amount ELSE 0 END), 0)
                """
            )
            if cur.fetchone() is not None:
                raise RuntimeError("Restored database contains an unbalanced posted financial transaction")

    return {
        "status": "verified",
        "backup_file": backup_file.name,
        "business_date": payload["business_date"],
        "alembic_revision": payload["alembic_revision"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or verify a La Serene HMS PostgreSQL backup")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--output-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    create.add_argument("--retain", type=int, default=7)

    verify = subparsers.add_parser("verify")
    verify.add_argument("backup_file", type=Path)
    verify.add_argument("manifest_file", type=Path)

    restore = subparsers.add_parser("restore-verify")
    restore.add_argument("backup_file", type=Path)
    restore.add_argument("manifest_file", type=Path)
    restore.add_argument("--target-database-url", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "create":
        dump_path, manifest_path = create_backup(args.output_dir, args.retain)
        print(json.dumps({"status": "created", "backup_file": str(dump_path), "manifest_file": str(manifest_path)}))
        return 0
    if args.command == "verify":
        payload = verify_backup(args.backup_file, args.manifest_file)
        print(json.dumps({"status": "verified", "backup_file": args.backup_file.name, "business_date": payload["business_date"]}))
        return 0
    result = restore_and_verify(args.backup_file, args.manifest_file, args.target_database_url)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
