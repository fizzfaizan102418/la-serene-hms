from datetime import date, datetime
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .auth import require_roles
from .backup import router as backup_router
from .db import get_db
from . import pms_core as _pms_core_models
from .pms_core import router as pms_core_router
from .housekeeping_control import (
    MaintenanceCreate,
    active_housekeeping_task,
    active_maintenance,
    create_housekeeping_task,
    create_maintenance_block,
    housekeeping_tasks,
    require_room,
    resolve_maintenance_block,
    router as housekeeping_control_router,
)
from . import housekeeping_hooks  # noqa: F401 - installs transactional dirty-room hook
from .inventory import lock_business_date
from .models import AuditLog, Reservation, ReservationRoom, Room, RoomType, User

router = APIRouter(prefix="", tags=["housekeeping"])
router.include_router(backup_router)
router.include_router(pms_core_router)
router.include_router(housekeeping_control_router)


@router.get("/housekeeping")
def housekeeping_board(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    business_date = lock_business_date(db)
    rows = db.execute(
        select(Room, RoomType.name)
        .join(RoomType, RoomType.id == Room.room_type_id)
        .order_by(Room.number)
    ).all()

    result = []
    for room, room_type_name in rows:
        active_reservation = db.execute(
            select(Reservation.id, Reservation.status, Reservation.check_out)
            .join(ReservationRoom, ReservationRoom.reservation_id == Reservation.id)
            .where(ReservationRoom.room_id == room.id, Reservation.status == "checked_in")
            .limit(1)
        ).first()
        task = active_housekeeping_task(db, room.id)
        block = active_maintenance(db, room.id)
        result.append({
            "room_id": room.id,
            "room_number": room.number,
            "room_type": room_type_name,
            "status": room.status,
            "housekeeping_task": dict(task) if task else None,
            "maintenance_block": dict(block) if block else None,
            "priority": "departure" if active_reservation else "maintenance" if block else "cleaning" if task else "ready" if room.status == "available" else "blocked" if room.status == "out_of_order" else room.status,
            "occupied_by_reservation_id": active_reservation[0] if active_reservation else None,
            "expected_release": str(active_reservation[2]) if active_reservation else None,
        })
    return {
        "business_date": business_date,
        "summary": {
            "occupied": sum(1 for row in result if row["status"] == "occupied"),
            "dirty": sum(1 for row in result if row["status"] == "dirty"),
            "available": sum(1 for row in result if row["status"] == "available"),
            "reserved": sum(1 for row in result if row["status"] == "reserved"),
            "out_of_order": sum(1 for row in result if row["status"] == "out_of_order"),
            "open_housekeeping_tasks": sum(1 for row in result if row["housekeeping_task"]),
            "active_maintenance": sum(1 for row in result if row["maintenance_block"]),
        },
        "rooms": result,
    }


@router.post("/housekeeping/rooms/{room_id}/clean")
def mark_room_clean(room_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "housekeeping"))):
    business_date = lock_business_date(db)
    room = require_room(db, room_id)
    if room.status != "dirty":
        raise HTTPException(status_code=409, detail=f"Room {room.number} is not awaiting cleaning")
    if active_maintenance(db, room.id):
        raise HTTPException(status_code=409, detail="Room has an active maintenance block")
    task = active_housekeeping_task(db, room.id)
    if task is None:
        task = create_housekeeping_task(db, room, business_date, user.id, "manual_clean", "Legacy clean operation", "normal")
    if task["status"] == "pending":
        now = datetime.utcnow()
        db.execute(update(housekeeping_tasks).where(housekeeping_tasks.c.id == task["id"]).values(status="in_progress", started_at=now, updated_at=now))
    elif task["status"] != "in_progress":
        raise HTTPException(status_code=409, detail="Housekeeping task cannot be completed")
    now = datetime.utcnow()
    room.status = "available"
    db.execute(update(housekeeping_tasks).where(housekeeping_tasks.c.id == task["id"]).values(status="completed", completed_at=now, completed_by=user.id, updated_at=now))
    db.add(AuditLog(user_id=user.id, action="housekeeping_clean", entity_type="room", entity_id=str(room.id), details=json.dumps({"room_number": room.number, "task_id": task["id"], "from": "dirty", "to": "available"})))
    db.commit()
    return {"room_id": room.id, "room_number": room.number, "status": room.status, "task_id": task["id"], "business_date": business_date}


@router.post("/housekeeping/rooms/{room_id}/out-of-order")
def mark_room_out_of_order(room_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    business_date = lock_business_date(db)
    room = require_room(db, room_id)
    if room.status in ("occupied", "reserved"):
        raise HTTPException(status_code=409, detail="Occupied or reserved rooms cannot be taken out of order")
    if active_maintenance(db, room.id):
        raise HTTPException(status_code=409, detail="Room already has an active maintenance block")
    row = create_maintenance_block(room.id, MaintenanceCreate(reason="Legacy out-of-order block", severity="normal"), db, user)
    return {"room_id": room.id, "room_number": room.number, "status": "out_of_order", "maintenance_block_id": row["id"], "business_date": business_date}


@router.post("/housekeeping/rooms/{room_id}/release")
def release_room_from_out_of_order(room_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    block = active_maintenance(db, room_id)
    if block is None:
        raise HTTPException(status_code=409, detail=f"Room {room_id} has no active maintenance block")
    result = resolve_maintenance_block(block["id"], db, user)
    return {"room_id": room_id, "status": result["room_status"], "maintenance_block_id": block["id"], "housekeeping_task_id": result["housekeeping_task_id"]}
