from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import DATA_DIR, SessionLocal, engine, ensure_schema_compatibility
from .models import AuditLog, User

router = APIRouter(prefix="/api/backup", tags=["backup"])
BACKUP_DIR = DATA_DIR / "backups"
DATABASE_PATH = DATA_DIR / "la_serene_hms.sqlite3"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
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


def create_backup(prefix: str = "backup") -> Path:
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


def cleanup_temp_file(path: Path) -> None:
    """Best-effort Windows-safe cleanup for uploaded restore files."""
    for attempt in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 4:
                # A transient antivirus/indexer lock must never turn a successful restore
                # into HTTP 500. The hidden temp file will be ignored by backup listing.
                return
            time.sleep(0.1 * (attempt + 1))


def backup_info(path: Path) -> dict:
    stat = path.stat()
    return {"filename": path.name, "size_bytes": stat.st_size, "created_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"}


@router.get("")
def list_backups(_: User = Depends(require_roles("admin"))):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted(BACKUP_DIR.glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)
    return {"database": DATABASE_PATH.name, "backups": [backup_info(path) for path in backups if not path.name.startswith('.')]} 


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
    if Path(filename).name != filename or not filename.endswith(".sqlite3") or filename.startswith('.'):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    target = BACKUP_DIR / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    valid, reason = validate_sqlite(target)
    if not valid:
        raise HTTPException(status_code=409, detail=f"Backup failed validation: {reason}")
    return FileResponse(target, media_type="application/vnd.sqlite3", filename=target.name)


@router.post("/restore")
async def restore_database(file: UploadFile = File(...), user: User = Depends(require_roles("admin"))):
    if not file.filename or Path(file.filename).suffix.lower() not in {".sqlite3", ".db", ".sqlite"}:
        raise HTTPException(status_code=400, detail="Upload a SQLite database file (.sqlite3, .db, or .sqlite)")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = BACKUP_DIR / f".restore_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.sqlite3"
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

        valid, reason = validate_sqlite(temp_path)
        if not valid:
            raise HTTPException(status_code=400, detail=f"Backup rejected: {reason}")

        safety_backup = create_backup("pre_restore")

        # Close pooled SQLAlchemy connections before replacing the database contents.
        engine.dispose()
        with sqlite3.connect(temp_path) as source:
            with sqlite3.connect(DATABASE_PATH) as destination:
                source.backup(destination)

        ensure_schema_compatibility()

        # Do not insert an audit row after restore: the restored database may belong to
        # a different installation and therefore may not contain the current admin user.
        return {
            "restored": True,
            "source_filename": file.filename,
            "safety_backup": backup_info(safety_backup),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc
    finally:
        await file.close()
        cleanup_temp_file(temp_path)
