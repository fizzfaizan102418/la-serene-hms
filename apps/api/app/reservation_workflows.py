from __future__ import annotations

from datetime import date, datetime
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import AuditLog, Folio, Reservation, ReservationRoom, Room, User
from .pms_core import Stay

router = APIRouter(prefix="/api", tags=["reservation-workflows"])

ACTIVE_STATUSES = ("reserved", "checked_in")
CANCELLABLE_STATUSES = ("reserved",)


class ReservationUpdate(BaseModel):
    check_in: date | None = None
    check_out: date | None = None
    guest_id: int | None = None
    notes: str | None = None


class RoomAssignmentsUpdate(BaseModel):
    room_ids: list[int] = Field(min_length=1)


class ActionReason(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ExtendStay(BaseModel):
    new_check_out: date


def audit(db: Session, user_id: int, action: str, entity_id: int, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type="reservation", entity_id=str(entity_id), details=json.dumps(details)))


def overlaps(db: Session, room_id: int, check_in: date, check_out: date, exclude_reservation_id: int | None = None) -> bool:
    stmt = (
        select(ReservationRoom.reservation_id)
        .join(Reservation, Reservation.id == ReservationRoom.reservation_id)
        .where(
            ReservationRoom.room_id == room_id,
            Reservation.status.in_(ACTIVE_STATUSES),
            Reservation.check_in < check_out,
            Reservation.check_out > check_in,
        )
        .limit(1)
    )
    if exclude_reservation_id is not None:
        stmt = stmt.where(Reservation.id != exclude_reservation_id)
    return db.scalar(stmt) is not None


def reservation_rooms(db: Session, reservation_id: int) -> list[int]:
    return db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation_id)).all()


@router.patch("/reservations/{reservation_id}")
def update_reservation(reservation_id: int, payload: ReservationUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Only active reservations can be modified")

    check_in = payload.check_in or reservation.check_in
    check_out = payload.check_out or reservation.check_out
    if check_out <= check_in:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if payload.guest_id is not None:
        from .models import Guest
        if not db.get(Guest, payload.guest_id):
            raise HTTPException(status_code=400, detail="Guest does not exist")
        reservation.guest_id = payload.guest_id
    if reservation.status == "checked_in" and check_in != reservation.check_in:
        raise HTTPException(status_code=409, detail="Check-in date cannot be changed after arrival")

    old = {"check_in": str(reservation.check_in), "check_out": str(reservation.check_out), "guest_id": reservation.guest_id, "notes": reservation.notes}
    current_rooms = reservation_rooms(db, reservation_id)
    conflicts = [db.get(Room, room_id).number for room_id in current_rooms if db.get(Room, room_id) and overlaps(db, room_id, check_in, check_out, reservation_id)]
    if conflicts:
        raise HTTPException(status_code=409, detail=f"Updated dates conflict with room(s): {', '.join(conflicts)}")

    reservation.check_in = check_in
    reservation.check_out = check_out
    reservation.notes = payload.notes

    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation_id)).all():
        stay.check_in = check_in
        stay.check_out = check_out

    audit(db, user.id, "update", reservation_id, {"from": old, "to": {"check_in": str(check_in), "check_out": str(check_out), "guest_id": reservation.guest_id, "notes": reservation.notes}})
    db.commit()
    return {"id": reservation.id, "check_in": reservation.check_in, "check_out": reservation.check_out, "guest_id": reservation.guest_id, "status": reservation.status, "room_ids": current_rooms, "notes": reservation.notes}


@router.put("/reservations/{reservation_id}/rooms")
def replace_reservation_rooms(reservation_id: int, payload: RoomAssignmentsUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "reserved":
        raise HTTPException(status_code=409, detail="Room allocation can only be changed before check-in")
    if len(payload.room_ids) != len(set(payload.room_ids)):
        raise HTTPException(status_code=400, detail="Duplicate room IDs are not allowed")

    old_ids = reservation_rooms(db, reservation_id)
    if not payload.room_ids:
        raise HTTPException(status_code=400, detail="At least one room is required")

    rooms = [db.get(Room, room_id) for room_id in payload.room_ids]
    if any(room is None for room in rooms):
        raise HTTPException(status_code=400, detail="One or more rooms do not exist")
    if any(room.status in ("dirty", "out_of_order") for room in rooms):
        raise HTTPException(status_code=409, detail="One or more rooms are not operationally bookable")

    conflicts = [room.number for room in rooms if overlaps(db, room.id, reservation.check_in, reservation.check_out, reservation_id)]
    if conflicts:
        raise HTTPException(status_code=409, detail=f"Room(s) unavailable for selected dates: {', '.join(conflicts)}")

    existing_links = db.scalars(select(ReservationRoom).where(ReservationRoom.reservation_id == reservation_id)).all()
    for link in existing_links:
        if link.room_id not in payload.room_ids:
            db.delete(link)
    existing_ids = set(old_ids)
    for room_id in payload.room_ids:
        if room_id not in existing_ids:
            db.add(ReservationRoom(reservation_id=reservation_id, room_id=room_id))

    audit(db, user.id, "room_allocation_update", reservation_id, {"from_room_ids": old_ids, "to_room_ids": payload.room_ids})
    db.commit()
    return {"reservation_id": reservation_id, "room_ids": payload.room_ids}


@router.post("/reservations/{reservation_id}/extend")
def extend_stay(reservation_id: int, payload: ExtendStay, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in":
        raise HTTPException(status_code=409, detail="Only an in-house stay can be extended")
    if payload.new_check_out <= reservation.check_in or payload.new_check_out <= date.today():
        raise HTTPException(status_code=400, detail="New check-out date is invalid")
    if payload.new_check_out <= reservation.check_out:
        raise HTTPException(status_code=400, detail="Use a later date to extend the stay")

    room_ids = reservation_rooms(db, reservation_id)
    conflicts = [db.get(Room, room_id).number for room_id in room_ids if db.get(Room, room_id) and overlaps(db, room_id, reservation.check_out, payload.new_check_out, reservation_id)]
    if conflicts:
        raise HTTPException(status_code=409, detail=f"Extension conflicts with room(s): {', '.join(conflicts)}")

    old_checkout = reservation.check_out
    reservation.check_out = payload.new_check_out
    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation_id)).all():
        stay.check_out = payload.new_check_out
    audit(db, user.id, "extend", reservation_id, {"from_check_out": str(old_checkout), "to_check_out": str(payload.new_check_out), "room_ids": room_ids})
    db.commit()
    return {"reservation_id": reservation_id, "old_check_out": old_checkout, "new_check_out": reservation.check_out, "room_ids": room_ids}


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int, payload: ActionReason | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status not in CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Only reservations awaiting check-in can be cancelled")
    reason = payload.reason if payload else None
    reservation.status = "cancelled"
    room_ids = reservation_rooms(db, reservation_id)
    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and room.status == "reserved":
            room.status = "available"
    audit(db, user.id, "cancel", reservation_id, {"reason": reason, "room_ids": room_ids, "cancelled_at": datetime.utcnow().isoformat()})
    db.commit()
    return {"reservation_id": reservation_id, "status": reservation.status, "room_ids": room_ids}


@router.post("/reservations/{reservation_id}/no-show")
def mark_no_show(reservation_id: int, payload: ActionReason | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "reserved":
        raise HTTPException(status_code=409, detail="Only a reserved arrival can be marked no-show")
    if reservation.check_in > date.today():
        raise HTTPException(status_code=409, detail="A future reservation cannot be marked no-show")
    reason = payload.reason if payload else None
    reservation.status = "no_show"
    room_ids = reservation_rooms(db, reservation_id)
    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and room.status == "reserved":
            room.status = "available"
    audit(db, user.id, "no_show", reservation_id, {"reason": reason, "room_ids": room_ids, "marked_at": datetime.utcnow().isoformat()})
    db.commit()
    return {"reservation_id": reservation_id, "status": reservation.status, "room_ids": room_ids}


@router.get("/reservations/{reservation_id}/availability-check")
def reservation_availability_check(reservation_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    room_ids = reservation_rooms(db, reservation_id)
    conflicts = []
    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and overlaps(db, room_id, reservation.check_in, reservation.check_out, reservation_id):
            conflicts.append(room.number)
    return {"reservation_id": reservation_id, "check_in": reservation.check_in, "check_out": reservation.check_out, "available": not conflicts, "conflicts": conflicts}
