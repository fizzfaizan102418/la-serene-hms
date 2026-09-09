from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

from ops.backup.backup import create_backup, normalize_postgres_url, restore_and_verify


def maintenance_url(database_url: str) -> str:
    parts = urlsplit(normalize_postgres_url(database_url))
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))


def with_database(database_url: str, database_name: str) -> str:
    parts = urlsplit(normalize_postgres_url(database_url))
    return urlunsplit((parts.scheme, parts.netloc, f"/{database_name}", parts.query, ""))


def main() -> None:
    source_url = normalize_postgres_url(os.environ["HMS_DATABASE_URL"])
    recovery_name = "la_serene_hms_recovery_ci"
    recovery_url = with_database(source_url, recovery_name)
    admin_url = maintenance_url(source_url)

    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{recovery_name}"')
            cur.execute(f'CREATE DATABASE "{recovery_name}"')

    try:
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp)
            dump_path, manifest_path = create_backup(backup_dir, retain=1)
            result = restore_and_verify(dump_path, manifest_path, recovery_url)
            assert result["status"] == "verified"
            print(result)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{recovery_name}"')


if __name__ == "__main__":
    main()
