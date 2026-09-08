from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import require_roles
from .db import Base, get_db
from .models import AuditLog, Folio, FolioItem, Guest, Payment, Reservation, ReservationRoom, Room, StayOccupant, User
from .pms_core import Stay

router = APIRouter(prefix="/api", tags=["stay-lifecycle"])
ACTIVE_STATUSES = ("reserved", "checked_in")


class StayFolioWindow(Base):
    __tablename__ = "stay_folio_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    folio_id: Mapped[int] = mapped_column(ForeignKey("folios.id", ondelete="CASCADE"), index=True)
    stay_id: Mapped[int] = mapped_column(ForeignKey("stays.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    payer_type: Mapped[str] = mapped_column(String(30), default="guest")
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("guests.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("booking_groups.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StayDatesUpdate(BaseModel):
    check_in: date
    check_out: date


class OccupantUpdate(BaseModel):
    role: str | None = Field(default=None, max_length=30)
    is_primary: bool | None = None
    check_in: date | None = None
    check_out: date | None = None
    notes: str | None = None


class RoomReplacement(BaseModel):
    to_room_id: int
    reason: str | None = Field(default=None, max_length=300)


class FolioWindowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    payer_type: str = Field(default="guest", max_length=30)
    guest_id: int | None = None
    group_id: int | None = None


class FolioWindowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    payer_type: str | None = Field(default=None, max_length=30)
    guest_id: int | None = None
    group_id: int | None = None
    status: str | None = Field(default=None, max_length=20)


def get_stay(db: Session, stay_id: int) -> Stay:
    stay = db.get(Stay, stay_id)
    if not stay:
        raise HTTPException(status_code=404, detail="Stay not found")
    return stay


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def room_conflict(db: Session, room_id: int, check_in: date, check_out: date, exclude_stay_id: int | None = None) -> bool:
    stmt = (
        select(Stay.id)
        .where(
            Stay.room_id == room_id,
            Stay.status.in_(ACTIVE_STATUSES),
            Stay.check_in < check_out,
            Stay.check_out > check_in,
        )
        .limit(1)
    )
    if exclude_stay_id is not None:
        stmt = stmt.where(Stay.id != exclude_stay_id)
    return db.scalar(stmt) is not None


def stay_folio_id(db: Session, stay_id: int) -> int:
    stay = get_stay(db, stay_id)
    folio_id = db.scalar(select(Folio.id).where(Folio.reservation_id == stay.reservation_id).order_by(Folio.id).limit(1))
    if folio_id is None:
        raise HTTPException(status_code=409, detail="No folio exists for this reservation")
    return folio_id


@router.get("/stays/{stay_id}/lifecycle")
def get_stay_lifecycle(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    stay = get_stay(db, stay_id)
    occupants = db.execute(
        select(StayOccupant, Guest.full_name)
        .join(Guest, Guest.id == StayOccupant.guest_id)
        .where(StayOccupant.stay_id == stay.id)
        .order_by(StayOccupant.is_primary.desc(), StayOccupant.id)
    ).all()
    windows = db.scalars(select(StayFolioWindow).where(StayFolioWindow.stay_id == stay.id).order_by(StayFolioWindow.id)).all()
    return {
        "stay": {"id": stay.id, "reservation_id": stay.reservation_id, "room_id": stay.room_id, "status": stay.status, "check_in": stay.check_in, "check_out": stay.check_out, "actual_check_in": stay.actual_check_in, "actual_check_out": stay.actual_check_out, "agreed_rate": stay.agreed_rate, "discount_percent": stay.discount_percent, "discount_amount": stay.discount_amount},
        "occupants": [{"id": o.id, "guest_id": o.guest_id, "guest_name": name, "role": o.role, "is_primary": o.is_primary, "check_in": o.check_in, "check_out": o.check_out, "notes": o.notes} for o, name in occupants],
        "folio_windows": [{"id": w.id, "folio_id": w.folio_id, "stay_id": w.stay_id, "name": w.name, "payer_type": w.payer_type, "guest_id": w.guest_id, "group_id": w.group_id, "status": w.status} for w in windows],
    }


@router.patch("/stays/{stay_id}/dates")
def update_stay_dates(stay_id: int, payload: StayDatesUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = get_stay(db, stay_id)
    reservation = db.get(Reservation, stay.reservation_id)
    if stay.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Only active stays can have dates changed")
    if not reservation:
        raise HTTPException(status_code=409, detail="Parent reservation not found")
    if payload.check_out <= payload.check_in:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if payload.check_in < reservation.check_in or payload.check_out > reservation.check_out:
        raise HTTPException(status_code=400, detail="Room stay dates must be within reservation dates")
    if stay.status == "checked_in" and payload.check_in != stay.check_in:
        raise HTTPException(status_code=409, detail="Check-in date cannot change after arrival")
    if room_conflict(db, stay.room_id, payload.check_in, payload.check_out, stay.id):
        raise HTTPException(status_code=409, detail="Room is reserved or occupied for part of the selected dates")
    old = {"check_in": str(stay.check_in), "check_out": str(stay.check_out)}
    stay.check_in, stay.check_out = payload.check_in, payload.check_out
    for occupant in db.scalars(select(StayOccupant).where(StayOccupant.stay_id == stay.id)).all():
        occupant.check_in = max(occupant.check_in or stay.check_in, stay.check_in)
        occupant.check_out = min(occupant.check_out or stay.check_out, stay.check_out)
        if occupant.check_out <= occupant.check_in:
            occupant.check_in, occupant.check_out = stay.check_in, stay.check_out
    audit(db, user.id, "stay_dates_update", "stay", stay.id, {"from": old, "to": {"check_in": str(stay.check_in), "check_out": str(stay.check_out)}})
    db.commit()
    return {"stay_id": stay.id, "check_in": stay.check_in, "check_out": stay.check_out}


@router.patch("/stays/{stay_id}/occupants/{occupant_id}")
def update_stay_occupant(stay_id: int, occupant_id: int, payload: OccupantUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = get_stay(db, stay_id)
    occupant = db.scalar(select(StayOccupant).where(StayOccupant.id == occupant_id, StayOccupant.stay_id == stay_id))
    if not occupant:
        raise HTTPException(status_code=404, detail="Stay occupant not found")
    check_in = payload.check_in or occupant.check_in or stay.check_in
    check_out = payload.check_out or occupant.check_out or stay.check_out
    if check_out <= check_in or check_in < stay.check_in or check_out > stay.check_out:
        raise HTTPException(status_code=400, detail="Occupant dates must be within the stay")
    if payload.is_primary:
        for other in db.scalars(select(StayOccupant).where(StayOccupant.stay_id == stay_id, StayOccupant.id != occupant_id)).all():
            other.is_primary = False
    if payload.role is not None:
        occupant.role = payload.role
    if payload.is_primary is not None:
        occupant.is_primary = payload.is_primary
    occupant.check_in, occupant.check_out = check_in, check_out
    if payload.notes is not None:
        occupant.notes = payload.notes
    audit(db, user.id, "occupant_update", "stay_occupant", occupant.id, {"stay_id": stay_id, "guest_id": occupant.guest_id})
    db.commit()
    return {"id": occupant.id, "stay_id": stay_id, "guest_id": occupant.guest_id, "role": occupant.role, "is_primary": occupant.is_primary, "check_in": occupant.check_in, "check_out": occupant.check_out, "notes": occupant.notes}


@router.delete("/stays/{stay_id}/occupants/{occupant_id}")
def remove_stay_occupant(stay_id: int, occupant_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = get_stay(db, stay_id)
    occupant = db.scalar(select(StayOccupant).where(StayOccupant.id == occupant_id, StayOccupant.stay_id == stay_id))
    if not occupant:
        raise HTTPException(status_code=404, detail="Stay occupant not found")
    occupants = db.scalars(select(StayOccupant).where(StayOccupant.stay_id == stay_id).order_by(StayOccupant.id)).all()
    if occupant.is_primary and len(occupants) == 1:
        raise HTTPException(status_code=409, detail="A stay must retain at least one occupant")
    was_primary = occupant.is_primary
    removed_guest_id = occupant.guest_id
    db.delete(occupant)
    if was_primary:
        replacement = next((item for item in occupants if item.id != occupant_id), None)
        if replacement:
            replacement.is_primary = True
    audit(db, user.id, "occupant_remove", "stay", stay.id, {"occupant_id": occupant_id, "guest_id": removed_guest_id})
    db.commit()
    return {"stay_id": stay_id, "removed_occupant_id": occupant_id}


@router.post("/stays/{stay_id}/replace-room", status_code=201)
def replace_stay_room(stay_id: int, payload: RoomReplacement, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = get_stay(db, stay_id)
    if stay.status != "reserved":
        raise HTTPException(status_code=409, detail="Reserved stays can be replaced; checked-in stays must use room move")
    source = db.get(Room, stay.room_id)
    target = db.get(Room, payload.to_room_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or destination room not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Destination room must be different")
    if target.status in ("dirty", "out_of_order", "occupied"):
        raise HTTPException(status_code=409, detail="Destination room is not available")
    if room_conflict(db, target.id, stay.check_in, stay.check_out, stay.id):
        raise HTTPException(status_code=409, detail="Destination room is reserved for the selected dates")
    link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == stay.reservation_id, ReservationRoom.room_id == source.id))
    if link:
        link.room_id = target.id
    else:
        db.add(ReservationRoom(reservation_id=stay.reservation_id, room_id=target.id))
    source_id = source.id
    stay.room_id = target.id
    if source.status == "reserved":
        source.status = "available"
    target.status = "reserved"
    audit(db, user.id, "room_replacement", "stay", stay.id, {"from_room_id": source_id, "to_room_id": target.id, "reason": payload.reason})
    db.commit()
    return {"stay_id": stay.id, "from_room_id": source_id, "to_room_id": target.id, "status": stay.status}


@router.post("/stays/{stay_id}/cancel")
def cancel_stay_room(stay_id: int, payload: RoomReplacement | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = get_stay(db, stay_id)
    if stay.status != "reserved":
        raise HTTPException(status_code=409, detail="Only reserved room stays can be cancelled")
    folio_id = db.scalar(select(Folio.id).where(Folio.reservation_id == stay.reservation_id).order_by(Folio.id).limit(1))
    if folio_id is not None:
        if db.scalar(select(FolioItem.id).where(FolioItem.folio_id == folio_id, FolioItem.stay_id == stay.id).limit(1)):
            raise HTTPException(status_code=409, detail="Room stay cannot be cancelled after financial charges have been posted")
        if db.scalar(select(Payment.id).where(Payment.folio_id == folio_id).limit(1)):
            raise HTTPException(status_code=409, detail="Room stay cannot be cancelled after payments have been posted")
    room_id = stay.room_id
    stay.status = "cancelled"
    link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == stay.reservation_id, ReservationRoom.room_id == room_id))
    if link:
        db.delete(link)
    room = db.get(Room, room_id)
    if room and room.status == "reserved":
        room.status = "available"
    audit(db, user.id, "stay_cancel", "stay", stay.id, {"room_id": room_id, "reason": getattr(payload, "reason", None)})
    db.commit()
    return {"stay_id": stay.id, "room_id": room_id, "status": stay.status}


@router.get("/stays/{stay_id}/folio-windows")
def list_stay_folio_windows(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    stay = get_stay(db, stay_id)
    windows = db.scalars(select(StayFolioWindow).where(StayFolioWindow.stay_id == stay.id).order_by(StayFolioWindow.id)).all()
    return [{"id": w.id, "folio_id": w.folio_id, "stay_id": w.stay_id, "name": w.name, "payer_type": w.payer_type, "guest_id": w.guest_id, "group_id": w.group_id, "status": w.status} for w in windows]


@router.post("/stays/{stay_id}/folio-windows", status_code=201)
def create_stay_folio_window(stay_id: int, payload: FolioWindowCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = get_stay(db, stay_id)
    window = StayFolioWindow(folio_id=stay_folio_id(db, stay.id), stay_id=stay.id, name=payload.name, payer_type=payload.payer_type, guest_id=payload.guest_id, group_id=payload.group_id)
    db.add(window); db.flush()
    audit(db, user.id, "folio_window_create", "stay_folio_window", window.id, {"stay_id": stay.id, "name": window.name})
    db.commit()
    return {"id": window.id, "folio_id": window.folio_id, "stay_id": window.stay_id, "name": window.name, "payer_type": window.payer_type, "guest_id": window.guest_id, "group_id": window.group_id, "status": window.status}


@router.patch("/stays/{stay_id}/folio-windows/{window_id}")
def update_stay_folio_window(stay_id: int, window_id: int, payload: FolioWindowUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = get_stay(db, stay_id)
    window = db.get(StayFolioWindow, window_id)
    if not window or window.stay_id != stay.id:
        raise HTTPException(status_code=404, detail="Folio window not found")
    for field in ("name", "payer_type", "guest_id", "group_id", "status"):
        value = getattr(payload, field)
        if value is not None:
            setattr(window, field, value)
    audit(db, user.id, "folio_window_update", "stay_folio_window", window.id, {"stay_id": stay.id})
    db.commit()
    return {"id": window.id, "folio_id": window.folio_id, "stay_id": window.stay_id, "name": window.name, "payer_type": window.payer_type, "guest_id": window.guest_id, "group_id": window.group_id, "status": window.status}
