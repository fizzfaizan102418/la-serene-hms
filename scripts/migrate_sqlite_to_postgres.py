"""Copy an existing La Serene SQLite installation into PostgreSQL.

Usage from apps/api after the PostgreSQL database has been created and migrated:

    set HMS_DATABASE_URL=postgresql+psycopg://hms_user:password@127.0.0.1:5432/la_serene_hms
    python ../../scripts/migrate_sqlite_to_postgres.py

Set HMS_SQLITE_PATH when the source database is not data/la_serene_hms.sqlite3.
The target must not contain application data. The script preserves primary keys and
re-seeds PostgreSQL identity sequences afterwards.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import MetaData, create_engine, text

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = BASE_DIR / "data" / "la_serene_hms.sqlite3"
SQLITE_PATH = Path(os.getenv("HMS_SQLITE_PATH", str(DEFAULT_SQLITE))).resolve()
DATABASE_URL = os.getenv("HMS_DATABASE_URL")

if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
    raise SystemExit("HMS_DATABASE_URL must be a PostgreSQL SQLAlchemy URL")
if not SQLITE_PATH.exists():
    raise SystemExit(f"SQLite source database not found: {SQLITE_PATH}")

source = create_engine(f"sqlite:///{SQLITE_PATH}")
target = create_engine(DATABASE_URL, pool_pre_ping=True)
source_meta = MetaData()
source_meta.reflect(bind=source)

tables_to_copy = [table for table in source_meta.sorted_tables if table.name != "alembic_version"]

with target.begin() as connection:
    existing_rows = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
    public_tables = {row[0] for row in existing_rows}
    application_tables = {table.name for table in tables_to_copy}
    populated = set()
    for table_name in application_tables & public_tables:
        count = connection.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
        if count:
            populated.add(table_name)
    if populated:
        raise SystemExit(f"Target contains existing application data: {', '.join(sorted(populated))}")

    for table in tables_to_copy:
        rows = source.connect().execute(table.select()).mappings().all()
        if not rows:
            continue
        target_table = table.to_metadata(MetaData())
        # Insert through the target reflected table so PostgreSQL receives native
        # SQLAlchemy values for dates, datetimes, numerics and booleans.
        target_meta = MetaData()
        target_table = target_meta.tables.get(table.name)
        if target_table is None:
            target_meta.reflect(bind=target, only=[table.name])
            target_table = target_meta.tables[table.name]
        connection.execute(target_table.insert(), [dict(row) for row in rows])
        print(f"migrated {table.name}: {len(rows)} row(s)")

    # Explicit primary keys do not advance PostgreSQL sequences. Reset every
    # conventional integer id sequence to MAX(id) so future inserts are safe.
    target_tables = target_meta.tables if 'target_meta' in locals() else {}
    for table_name in application_tables & public_tables:
        columns = {row[0] for row in connection.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table"), {"table": table_name})}
        if "id" not in columns:
            continue
        sequence = connection.execute(text("SELECT pg_get_serial_sequence(:table_name, 'id')"), {"table_name": table_name}).scalar_one_or_none()
        if sequence:
            connection.execute(text(f"SELECT setval('{sequence}', COALESCE((SELECT MAX(id) FROM \"{table_name}\"), 1), true)"))

print(f"Migration complete: {SQLITE_PATH} -> PostgreSQL")
