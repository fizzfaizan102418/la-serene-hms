from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import AuditLog, Reservation, ReservationRoom, Room, RoomType, User

router = APIRouter(prefix="/api", tags=["housekeeping"])


@router.get("/housekeeping")
def housekeeping_board(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    rows = db.execute(
        select(Room, RoomType.name)
        .join(RoomType, RoomType.id == Room.room_type_id)
        .where(Room.status.in_(("dirty", "available", "out_of_order")))
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
        result.append({
            "room_id": room.id,
            "room_number": room.number,
            "room_type": room_type_name,
            "status": room.status,
            "priority": "departure" if active_reservation else ("cleaning" if room.status == "dirty" else "ready" if room.status == "available" else "blocked"),
            "occupied_by_reservation_id": active_reservation[0] if active_reservation else None,
            "expected_release": str(active_reservation[2]) if active_reservation else None,
        })
    return {
        "business_date": __import__("datetime").date.today(),
        "summary": {
            "dirty": sum(1 for row in result if row["status"] == "dirty"),
            "available": sum(1 for row in result if row["status"] == "available"),
            "out_of_order": sum(1 for row in result if row["status"] == "out_of_order"),
        },
        "rooms": result,
    }


@router.post("/housekeeping/rooms/{room_id}/clean")
def mark_room_clean(room_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "housekeeping"))):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.status != "dirty":
        raise HTTPException(status_code=409, detail=f"Room {room.number} is not awaiting cleaning")

    previous_status = room.status
    room.status = "available"
    db.add(AuditLog(
        user_id=user.id,
        action="housekeeping_clean",
        entity_type="room",
        entity_id=str(room.id),
        details=f'{{"room_number":"{room.number}","from":"{previous_status}","to":"available"}}',
    ))
    db.commit()
    db.refresh(room)
    return {"room_id": room.id, "room_number": room.number, "status": room.status}


@router.post("/housekeeping/rooms/{room_id}/out-of-order")
def mark_room_out_of_order(room_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.status in ("occupied", "reserved"):
        raise HTTPException(status_code=409, detail="Occupied or reserved rooms cannot be taken out of order")

    room.status = "out_of_order"
    db.add(AuditLog(
        user_id=user.id,
        action="room_out_of_order",
        entity_type="room",
        entity_id=str(room.id),
        details=f'{{"room_number":"{room.number}","to":"out_of_order"}}',
    ))
    db.commit()
    db.refresh(room)
    return {"room_id": room.id, "room_number": room.number, "status": room.status}


@router.post("/housekeeping/rooms/{room_id}/release")
def release_room_from_out_of_order(room_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.status != "out_of_order":
        raise HTTPException(status_code=409, detail=f"Room {room.number} is not out of order")

    room.status = "available"
    db.add(AuditLog(
        user_id=user.id,
        action="room_released",
        entity_type="room",
        entity_id=str(room.id),
        details=f'{{"room_number":"{room.number}","to":"available"}}',
    ))
    db.commit()
    db.refresh(room)
    return {"room_id": room.id, "room_number": room.number, "status": room.status}
