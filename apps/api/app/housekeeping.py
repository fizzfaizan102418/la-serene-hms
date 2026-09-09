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


@app.get("/housekeeping")
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