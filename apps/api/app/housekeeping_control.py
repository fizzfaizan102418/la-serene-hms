from __future__ import annotations

from datetime import datetime
import json
from secrets import token_hex

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Table, func, insert, select, update
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import Base, get_db
from .inventory import lock_business_date
from .models import AuditLog, Reservation, ReservationRoom, Room, User

router = APIRouter(tags=["housekeeping-control"])

housekeeping_tasks = Table(
    "housekeeping_tasks", Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("task_no", String(40), unique=True, index=True, nullable=False),
    Column("room_id", Integer, ForeignKey("rooms.id", ondelete="RESTRICT"), index=True, nullable=False),
    Column("business_date", Date, index=True, nullable=False),
    Column("task_type", String(40), nullable=False),
    Column("status", String(30), nullable=False),
    Column("priority", String(20), nullable=False, server_default="normal"),
    Column("reason", String(300), nullable=False),
    Column("assigned_to", Integer, ForeignKey("users.id"), nullable=True),
    Column("started_at", DateTime, nullable=True),
    Column("completed_at", DateTime, nullable=True),
    Column("completed_by", Integer, ForeignKey("users.id"), nullable=True),
    Column("created_by", Integer, ForeignKey("users.id"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
)

maintenance_blocks = Table(
    "maintenance_blocks", Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("block_no", String(40), unique=True, index=True, nullable=False),
    Column("room_id", Integer, ForeignKey("rooms.id", ondelete="RESTRICT"), index=True, nullable=False),
    Column("business_date", Date, index=True, nullable=False),
    Column("status", String(20), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("severity", String(20), nullable=False, server_default="normal"),
    Column("reported_by", Integer, ForeignKey("users.id"), nullable=False),
    Column("resolved_by", Integer, ForeignKey("users.id"), nullable=True),
    Column("resolved_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
    Column("updated_at", DateTime, nullable=False, default=datetime.utcnow),
)


class HousekeepingTaskCreate(BaseModel):
    task_type: str = Field(default="manual_clean", min_length=1, max_length=40)
    reason: str = Field(default="Manual housekeeping task", min_length=1, max_length=300)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")


class MaintenanceCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    severity: str = Field(default="normal", pattern="^(normal|high|critical)$")


class TaskAssign(BaseModel):
    assigned_to: int


def rowdict(row) -> dict:
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details, default=str)))


def active_maintenance(db: Session, room_id: int):
    return db.execute(select(maintenance_blocks).where(maintenance_blocks.c.room_id == room_id, maintenance_blocks.c.status == "active").with_for_update()).mappings().first()


def active_housekeeping_task(db: Session, room_id: int):
    return db.execute(
        select(housekeeping_tasks).where(
            housekeeping_tasks.c.room_id == room_id,
            housekeeping_tasks.c.status.in_(("pending", "in_progress")),
        ).order_by(housekeeping_tasks.c.id.desc()).with_for_update()
    ).mappings().first()


def create_housekeeping_task(db: Session, room: Room, business_date, user_id: int | None, task_type: str, reason: str, priority: str = "normal") -> dict:
    existing = active_housekeeping_task(db, room.id)
    if existing:
        return rowdict(existing)
    row = db.execute(insert(housekeeping_tasks).values(
        task_no=f"HK-{business_date.strftime('%Y%m%d')}-{token_hex(4).upper()}",
        room_id=room.id,
        business_date=business_date,
        task_type=task_type,
        status="pending",
        priority=priority,
        reason=reason[:300],
        created_by=user_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ).returning(housekeeping_tasks)).mappings().one()
    return rowdict(row)


def room_occupied_or_reserved(db: Session, room_id: int) -> bool:
    return db.scalar(
        select(Reservation.id)
        .join(ReservationRoom, ReservationRoom.reservation_id == Reservation.id)
        .where(
            ReservationRoom.room_id == room_id,
            Reservation.status.in_(("reserved", "checked_in")),
        ).limit(1)
    ) is not None


def require_room(db: Session, room_id: int) -> Room:
    room = db.scalar(select(Room).where(Room.id == room_id).with_for_update())
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    return room


@router.get("/housekeeping/tasks")
def list_housekeeping_tasks(status: str | None = None, room_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    stmt = select(housekeeping_tasks).order_by(housekeeping_tasks.c.id.desc()).limit(500)
    if status:
        stmt = stmt.where(housekeeping_tasks.c.status == status)
    if room_id is not None:
        stmt = stmt.where(housekeeping_tasks.c.room_id == room_id)
    return [dict(row) for row in db.execute(stmt).mappings().all()]


@router.post("/housekeeping/rooms/{room_id}/tasks", status_code=201)
def create_task(room_id: int, payload: HousekeepingTaskCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "housekeeping"))):
    business_date = lock_business_date(db)
    room = require_room(db, room_id)
    if active_maintenance(db, room.id):
        raise HTTPException(status_code=409, detail="Room has an active maintenance block")
    if payload.task_type == "manual_clean" and room.status != "dirty":
        raise HTTPException(status_code=409, detail="Manual cleaning tasks can only be created for dirty rooms")
    if room.status not in ("dirty", "available"):
        raise HTTPException(status_code=409, detail="Housekeeping tasks can only be created for dirty or available rooms")
    task = create_housekeeping_task(db, room, business_date, user.id, payload.task_type, payload.reason, payload.priority)
    audit(db, user.id, "housekeeping_task_created", "housekeeping_task", task["id"], {"room_id": room.id, "task_type": payload.task_type})
    db.commit()
    return task


@router.post("/housekeeping/tasks/{task_id}/assign")
def assign_task(task_id: int, payload: TaskAssign, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "housekeeping"))):
    task = db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id).with_for_update()).mappings().first()
    if not task:
        raise HTTPException(status_code=404, detail="Housekeeping task not found")
    assignee = db.get(User, payload.assigned_to)
    if not assignee:
        raise HTTPException(status_code=404, detail="Assigned user not found")
    if task["status"] not in ("pending", "in_progress"):
        raise HTTPException(status_code=409, detail="Completed or cancelled tasks cannot be reassigned")
    db.execute(update(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id).values(assigned_to=assignee.id, updated_at=datetime.utcnow()))
    audit(db, user.id, "housekeeping_task_assigned", "housekeeping_task", task_id, {"assigned_to": assignee.id})
    db.commit()
    return dict(db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id)).mappings().one())


@router.post("/housekeeping/tasks/{task_id}/start")
def start_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "housekeeping"))):
    business_date = lock_business_date(db)
    task = db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id).with_for_update()).mappings().first()
    if not task:
        raise HTTPException(status_code=404, detail="Housekeeping task not found")
    if task["business_date"] != business_date:
        raise HTTPException(status_code=409, detail="Housekeeping task belongs to a different business date")
    room = require_room(db, task["room_id"])
    if active_maintenance(db, room.id):
        raise HTTPException(status_code=409, detail="Room has an active maintenance block")
    if task["status"] != "pending":
        raise HTTPException(status_code=409, detail="Only pending housekeeping tasks can be started")
    if room.status != "dirty":
        raise HTTPException(status_code=409, detail="Room must be dirty before cleaning starts")
    now = datetime.utcnow()
    db.execute(update(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id).values(status="in_progress", started_at=now, updated_at=now))
    audit(db, user.id, "housekeeping_task_started", "housekeeping_task", task_id, {"room_id": room.id})
    db.commit()
    return dict(db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id)).mappings().one())


@router.post("/housekeeping/tasks/{task_id}/complete")
def complete_task(task_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "housekeeping"))):
    business_date = lock_business_date(db)
    task = db.execute(select(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id).with_for_update()).mappings().first()
    if not task:
        raise HTTPException(status_code=404, detail="Housekeeping task not found")
    if task["business_date"] != business_date:
        raise HTTPException(status_code=409, detail="Housekeeping task belongs to a different business date")
    room = require_room(db, task["room_id"])
    if active_maintenance(db, room.id):
        raise HTTPException(status_code=409, detail="Room has an active maintenance block")
    if task["status"] != "in_progress":
        raise HTTPException(status_code=409, detail="Housekeeping task must be in progress before completion")
    if room.status != "dirty":
        raise HTTPException(status_code=409, detail="Room state is not dirty; refusing to mark it clean")
    if room_occupied_or_reserved(db, room.id):
        raise HTTPException(status_code=409, detail="Room has an active reservation or occupancy")
    now = datetime.utcnow()
    room.status = "available"
    db.execute(update(housekeeping_tasks).where(housekeeping_tasks.c.id == task_id).values(status="completed", completed_at=now, completed_by=user.id, updated_at=now))
    audit(db, user.id, "housekeeping_task_completed", "housekeeping_task", task_id, {"room_id": room.id, "room_status": "available"})
    db.commit()
    return {"task_id": task_id, "room_id": room.id, "status": "completed", "room_status": room.status, "completed_at": now}


@router.get("/maintenance/blocks")
def list_maintenance_blocks(status: str | None = None, room_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    stmt = select(maintenance_blocks).order_by(maintenance_blocks.c.id.desc()).limit(500)
    if status:
        stmt = stmt.where(maintenance_blocks.c.status == status)
    if room_id is not None:
        stmt = stmt.where(maintenance_blocks.c.room_id == room_id)
    return [dict(row) for row in db.execute(stmt.mappings()).all()]


@router.post("/maintenance/rooms/{room_id}/blocks", status_code=201)
def create_maintenance_block(room_id: int, payload: MaintenanceCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    business_date = lock_business_date(db)
    room = require_room(db, room_id)
    if room.status in ("occupied", "reserved") or room_occupied_or_reserved(db, room.id):
        raise HTTPException(status_code=409, detail="Occupied or reserved rooms cannot be maintenance-blocked")
    if active_maintenance(db, room.id):
        raise HTTPException(status_code=409, detail="Room already has an active maintenance block")
    row = db.execute(insert(maintenance_blocks).values(
        block_no=f"MNT-{business_date.strftime('%Y%m%d')}-{token_hex(4).upper()}",
        room_id=room.id, business_date=business_date, status="active",
        reason=payload.reason, severity=payload.severity, reported_by=user.id,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ).returning(maintenance_blocks)).mappings().one()
    room.status = "out_of_order"
    audit(db, user.id, "maintenance_block_created", "maintenance_block", row["id"], {"room_id": room.id, "severity": payload.severity})
    db.commit()
    return dict(row)


@router.post("/maintenance/blocks/{block_id}/resolve")
def resolve_maintenance_block(block_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    business_date = lock_business_date(db)
    block = db.execute(select(maintenance_blocks).where(maintenance_blocks.c.id == block_id).with_for_update()).mappings().first()
    if not block:
        raise HTTPException(status_code=404, detail="Maintenance block not found")
    if block["status"] != "active":
        raise HTTPException(status_code=409, detail="Maintenance block is already resolved")
    if block["business_date"] != business_date:
        raise HTTPException(status_code=409, detail="Maintenance block belongs to a different business date")
    room = require_room(db, block["room_id"])
    if room.status != "out_of_order":
        raise HTTPException(status_code=409, detail="Room status is out of sync with active maintenance block")
    now = datetime.utcnow()
    db.execute(update(maintenance_blocks).where(maintenance_blocks.c.id == block_id).values(status="resolved", resolved_by=user.id, resolved_at=now, updated_at=now))
    room.status = "dirty"
    task = create_housekeeping_task(db, room, business_date, user.id, "maintenance_clean", "Cleaning required after maintenance release", "high")
    audit(db, user.id, "maintenance_block_resolved", "maintenance_block", block_id, {"room_id": room.id, "housekeeping_task_id": task["id"]})
    db.commit()
    return {"block_id": block_id, "room_id": room.id, "status": "resolved", "room_status": room.status, "housekeeping_task_id": task["id"]}


@router.get("/rooms/{room_id}/operational-state")
def room_operational_state(room_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    task = active_housekeeping_task(db, room.id)
    block = active_maintenance(db, room.id)
    occupancy = db.execute(
        select(Reservation.id, Reservation.status)
        .join(ReservationRoom, ReservationRoom.reservation_id == Reservation.id)
        .where(ReservationRoom.room_id == room.id, Reservation.status.in_(("reserved", "checked_in")))
        .limit(5)
    ).all()
    return {
        "room_id": room.id,
        "room_status": room.status,
        "maintenance_block": dict(block) if block else None,
        "housekeeping_task": dict(task) if task else None,
        "active_reservations": [{"reservation_id": row[0], "status": row[1]} for row in occupancy],
        "consistent": not (
            (block and room.status != "out_of_order")
            or (task and room.status != "dirty")
            or (room.status == "occupied" and not any(row[1] == "checked_in" for row in occupancy))
        ),
    }
