from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import DATA_DIR, DATABASE_URL, IS_SQLITE, SessionLocal, engine, ensure_schema_compatibility
from .models import AuditLog, User

router = APIRouter(prefix="/api/backup", tags=["backup"])
BACKUP_DIR = DATA_DIR / "backups"
DATABASE_PATH = DATA_DIR / "la_serene_hms.sqlite3"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
POSTGRES_BACKUP_SUFFIX = ".dump"
REQUIRED_TABLES = {"roles", "users", "room_types", "rooms", "guests", "reservations", "reservation_rooms", "folios", "folio_items", "payments", "expenses", "audit_logs"}


def audit(db: Session, user_id: int, action: str, details: dict):
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type="backup", entity_id=None, details=json.dumps(details)))


def validate_sqlite(path: Path) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size < 100:
        return False, "Backup file is empty or too small"
    try:
        with sqlite3.connect(path) as connection:
            if connection.execute("PRAGMA schema_version").fetchone() is None:
                return False, "Not a valid SQLite database"
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                return False, "SQLite integrity check failed"
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                return False, f"Backup is missing required tables: {', '.join(sorted(missing))}"
    except sqlite3.DatabaseError as exc:
        return False, f"Invalid SQLite database: {exc}"
    return True, "ok"


def postgres_command_url() -> tuple[list[str], dict[str, str]]:
    """Build explicit libpq connection arguments from the configured DB URL.

    The Windows production API runs as LocalSystem. libpq can otherwise inherit
    machine/service environment values such as PGUSER and accidentally connect as
    SYSTEM. Passing the configured host/port/user/database explicitly prevents
    that while keeping the password out of the process command line.
    """
    url = make_url(DATABASE_URL)
    password = url.password
    if not url.username or not url.host or not url.database:
        raise RuntimeError("Production PostgreSQL connection is incomplete")
    if not password:
        raise RuntimeError("Production PostgreSQL connection password is not configured")

    args = ["--host", url.host]
    if url.port:
        args += ["--port", str(url.port)]
    args += ["--username", url.username, "--dbname", url.database]

    env = os.environ.copy()
    # Do not allow service-account PostgreSQL defaults to override the production
    # connection. PGPASSWORD is intentionally retained as the only credential
    # supplied to pg_dump/pg_restore.
    for key in ("PGUSER", "PGDATABASE", "PGHOST", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE"):
        env.pop(key, None)
    env["PGPASSWORD"] = password
    return args, env


def run_postgres_tool(command: list[str], env: dict[str, str]) -> None:
    try:
        completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("PostgreSQL client tools (pg_dump/pg_restore) are not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("PostgreSQL backup operation timed out after 5 minutes") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "PostgreSQL command failed").strip()
        raise RuntimeError(detail[-2000:])


def validate_postgres(path: Path) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size < 100:
        return False, "Backup file is empty or too small"
    pg_restore = shutil.which("pg_restore")
    if not pg_restore:
        return False, "PostgreSQL client tool pg_restore is not installed or not on PATH"
    try:
        completed = subprocess.run([pg_restore, "--list", str(path)], capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return False, "PostgreSQL backup validation timed out"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Invalid PostgreSQL dump").strip()
        return False, detail[-2000:]
    if not completed.stdout.strip():
        return False, "PostgreSQL dump contains no objects"
    return True, "ok"


def create_sqlite_backup(prefix: str = "backup") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.sqlite3"
    engine.dispose()
    with sqlite3.connect(DATABASE_PATH) as source:
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    valid, reason = validate_sqlite(target)
    if not valid:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Created backup did not pass validation: {reason}")
    return target


def create_postgres_backup(prefix: str = "backup") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')
    target = BACKUP_DIR / f"{prefix}_{timestamp}{POSTGRES_BACKUP_SUFFIX}"
    partial = BACKUP_DIR / f".{target.name}.partial"
    connection_args, env = postgres_command_url()
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("PostgreSQL client tool pg_dump is not installed or not on PATH")
    partial.unlink(missing_ok=True)
    try:
        run_postgres_tool(
            [pg_dump, "--format=custom", "--no-owner", "--no-acl", *connection_args, "--file", str(partial)],
            env,
        )
        valid, reason = validate_postgres(partial)
        if not valid:
            raise RuntimeError(f"Created backup did not pass validation: {reason}")
        partial.replace(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise


def create_backup(prefix: str = "backup") -> Path:
    return create_sqlite_backup(prefix) if IS_SQLITE else create_postgres_backup(prefix)


def cleanup_temp_file(path: Path) -> None:
    """Best-effort Windows-safe cleanup for uploaded restore files."""
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 4:
                return
            time.sleep(0.1 * (attempt + 1))


def backup_info(path: Path) -> dict:
    stat = path.stat()
    return {"filename": path.name, "size_bytes": stat.st_size, "created_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"}


@router.get("")
def list_backups(_: User = Depends(require_roles("admin"))):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "*.sqlite3" if IS_SQLITE else f"*{POSTGRES_BACKUP_SUFFIX}"
    backups = sorted(BACKUP_DIR.glob(suffix), key=lambda path: path.stat().st_mtime, reverse=True)
    if IS_SQLITE:
        valid_backups = [path for path in backups if not path.name.startswith('.') and validate_sqlite(path)[0]]
    else:
        valid_backups = [path for path in backups if not path.name.startswith('.') and validate_postgres(path)[0]]
    return {"database": DATABASE_PATH.name if IS_SQLITE else "PostgreSQL", "backups": [backup_info(path) for path in valid_backups]}


@router.post("")
def create_database_backup(user: User = Depends(require_roles("admin"))):
    try:
        target = create_backup("backup")
        with SessionLocal() as db:
            audit(db, user.id, "create", {"filename": target.name, "size_bytes": target.stat().st_size})
            db.commit()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to create backup: {exc}") from exc
    return backup_info(target)


@router.get("/{filename}/download")
def download_backup(filename: str, _: User = Depends(require_roles("admin"))):
    suffix = ".sqlite3" if IS_SQLITE else POSTGRES_BACKUP_SUFFIX
    if Path(filename).name != filename or not filename.endswith(suffix) or filename.startswith('.'):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    target = BACKUP_DIR / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    if IS_SQLITE:
        valid, reason = validate_sqlite(target)
        media_type = "application/vnd.sqlite3"
    else:
        valid, reason = validate_postgres(target)
        media_type = "application/octet-stream"
    if not valid:
        raise HTTPException(status_code=409, detail=f"Backup failed validation: {reason}")
    return FileResponse(target, media_type=media_type, filename=target.name)


@router.post("/restore")
async def restore_database(file: UploadFile = File(...), user: User = Depends(require_roles("admin"))):
    if IS_SQLITE:
        if not file.filename or Path(file.filename).suffix.lower() not in {".sqlite3", ".db", ".sqlite"}:
            raise HTTPException(status_code=400, detail="Upload a SQLite database file (.sqlite3, .db, or .sqlite)")
    else:
        if not file.filename or Path(file.filename).suffix.lower() not in {".dump", ".backup"}:
            raise HTTPException(status_code=400, detail="Upload a PostgreSQL custom-format dump (.dump or .backup)")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = ".sqlite3" if IS_SQLITE else POSTGRES_BACKUP_SUFFIX
    temp_path = BACKUP_DIR / f".restore_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}{suffix}"
    bytes_written = 0
    try:
        with temp_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Backup file exceeds 100 MB limit")
                output.write(chunk)

        valid, reason = validate_sqlite(temp_path) if IS_SQLITE else validate_postgres(temp_path)
        if not valid:
            raise HTTPException(status_code=400, detail=f"Backup rejected: {reason}")

        safety_backup = create_backup("pre_restore")

        if IS_SQLITE:
            engine.dispose()
            with sqlite3.connect(temp_path) as source:
                with sqlite3.connect(DATABASE_PATH) as destination:
                    source.backup(destination)
            ensure_schema_compatibility()
        else:
            connection_args, env = postgres_command_url()
            pg_restore = shutil.which("pg_restore")
            if not pg_restore:
                raise RuntimeError("PostgreSQL client tool pg_restore is not installed or not on PATH")
            engine.dispose()
            run_postgres_tool(
                [pg_restore, "--clean", "--if-exists", "--no-owner", "--no-acl", *connection_args, str(temp_path)],
                env,
            )

        return {"restored": True, "source_filename": file.filename, "safety_backup": backup_info(safety_backup)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc
    finally:
        await file.close()
        cleanup_temp_file(temp_path)
