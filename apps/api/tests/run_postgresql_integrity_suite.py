from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    print("$", " ".join(command))
    completed = subprocess.run(command, cwd=cwd, env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def read_env_value(env_file: Path, key_name: str) -> str:
    if not env_file.exists():
        return ""
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() == key_name:
            return raw_value.strip().strip('"').strip("'")
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PostgreSQL destructive integrity tests in an isolated database.")
    default_root = Path(r"C:\LaSereneHMS") if os.name == "nt" else None
    parser.add_argument(
        "--production-root",
        type=Path,
        default=default_root,
        help="Installed production root containing apps/api/.env (default: C:\\LaSereneHMS on Windows).",
    )
    return parser.parse_args()


def load_database_url(production_root: Path | None) -> str:
    value = os.environ.get("HMS_DATABASE_URL", "").strip()
    if value:
        return value

    candidates: list[Path] = []
    if production_root is not None:
        candidates.append(production_root / "apps" / "api" / ".env")
    candidates.append(API_ROOT / ".env")

    for env_file in candidates:
        value = read_env_value(env_file, "HMS_DATABASE_URL")
        if value:
            return value
    return ""


def load_admin_database_url() -> str:
    """Return a privileged PostgreSQL connection supplied only for test DB lifecycle operations."""
    return os.environ.get("HMS_POSTGRES_ADMIN_URL", "").strip()


def main() -> int:
    args = parse_args()
    os.chdir(ROOT)
    source_url = load_database_url(args.production_root)
    admin_url = load_admin_database_url()

    if not source_url.startswith(("postgresql://", "postgresql+psycopg://")):
        print("ERROR: HMS_DATABASE_URL must point to PostgreSQL.")
        return 2
    if not admin_url:
        print("ERROR: HMS_POSTGRES_ADMIN_URL is required to create the disposable PostgreSQL test database.")
        print("       Keep the production application account unchanged; supply a privileged PostgreSQL connection only for this test run.")
        return 2
    if not admin_url.startswith(("postgresql://", "postgresql+psycopg://")):
        print("ERROR: HMS_POSTGRES_ADMIN_URL must point to PostgreSQL.")
        return 2

    source = make_url(source_url)
    source_db = source.database
    source_user = source.username
    if not source_db:
        print("ERROR: HMS_DATABASE_URL has no database name.")
        return 2
    if not source_user:
        print("ERROR: HMS_DATABASE_URL has no database username.")
        return 2

    admin = make_url(admin_url)
    if not admin.database:
        admin = admin.set(database="postgres")

    test_db = f"la_serene_hms_pg_integrity_{uuid.uuid4().hex[:10]}"
    if test_db == source_db:
        print("ERROR: Refusing to use the source database as the destructive test database.")
        return 2

    maintenance = admin.set(database="postgres")
    admin_engine = create_engine(maintenance, future=True, pool_pre_ping=True)
    test_url: URL = source.set(database=test_db)
    created = False

    try:
        try:
            # Make the disposable database owned by the same role used by HMS_DATABASE_URL.
            # This preserves the production app role's least privilege while allowing Alembic
            # to create its tables in the isolated test database. The admin connection is used
            # only for database lifecycle operations.
            owner_identifier = source_user.replace('"', '""')
            with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text(f'CREATE DATABASE "{test_db}" OWNER "{owner_identifier}"'))
        except SQLAlchemyError as exc:
            print("ERROR: Could not create the disposable PostgreSQL test database.")
            print(f"       {exc.__class__.__name__}: {str(exc).splitlines()[0]}")
            print("       Verify HMS_POSTGRES_ADMIN_URL uses a PostgreSQL role with CREATEDB or equivalent database-admin privileges and that the HMS application role exists.")
            return 2

        created = True
        print(f"Created isolated PostgreSQL test database: {test_db}")

        env = os.environ.copy()
        env["HMS_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
        env["HMS_ENVIRONMENT"] = "test"
        env["PYTHONPATH"] = str(API_ROOT)

        run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API_ROOT, env=env)
        run(
            [sys.executable, "-m", "unittest", "tests.test_postgresql_data_integrity_destructive", "-v"],
            cwd=API_ROOT,
            env={**env, "HMS_TEST_DATABASE_URL": env["HMS_DATABASE_URL"]},
        )
        print("PostgreSQL data-integrity suite: PASS")
        return 0
    finally:
        if created:
            try:
                with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) "
                            "FROM pg_stat_activity "
                            "WHERE datname = :database AND pid <> pg_backend_pid()"
                        ),
                        {"database": test_db},
                    )
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{test_db}"'))
                print(f"Removed isolated PostgreSQL test database: {test_db}")
            except Exception as exc:  # pragma: no cover - cleanup failure is reported explicitly
                print(f"WARNING: Could not remove test database {test_db}: {exc}")
        admin_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
