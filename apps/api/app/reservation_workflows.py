from __future__ import annotations

from datetime import date, datetime
import json
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import AuditLog, Folio, Guest, Reservation, ReservationRoom, Room, StayOccupant, StayRateSegment, User
from .pms_core import BookingGroup, GroupReservation, Stay

router = APIRouter(prefix="/api", tags=["reservation-workflows"])
ACTIVE_STATUSES = ("reserved", "checked_in")
CANCELLABLE_STATUSES = ("reserved",)
MONEY = Decimal("0.01")


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


class ReservationRoomSetup(BaseModel):
    room_id: int
    occupant_guest_id: int | None = None
    agreed_rate: Decimal = Field(ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    fixed_discount: Decimal = Field(default=Decimal("0"), ge=0)


class ReservationWorkflowCreate(BaseModel):
    guest_id: int
    check_in: date
    check_out: date
    rooms: list[ReservationRoomSetup] = Field(min_length=1)
    group_id: int | None = None
    notes: str | None = None
    payment_policy: str = Field(default="at_checkout", pattern="^(at_booking|at_checkin|at_checkout|partial)$")
    deposit_received: Decimal = Field(default=Decimal("0"), ge=0)


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def discount_values(rate: Decimal, percent: Decimal, fixed: Decimal) -> tuple[Decimal, Decimal]:
    if percent and fixed:
        raise HTTPException(status_code=400, detail="Use either percentage or fixed discount, not both")
    gross = money(rate)
    discount = money(gross * percent / Decimal("100")) if percent else money(fixed)
    discount = min(discount, gross)
    return discount, money(gross - discount)


def audit(db: Session, user_id: int, action: str, entity_id: int, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type="reservation", entity_id=str(entity_id), details=json.dumps(details)))


def overlaps(db: Session, room_id: int, check_in: date, check_out: date, exclude_reservation_id: int | None = None) -> bool:
    stmt = (select(ReservationRoom.reservation_id).join(Reservation, Reservation.id == ReservationRoom.reservation_id).where(ReservationRoom.room_id == room_id, Reservation.status.in_(ACTIVE_STATUSES), Reservation.check_in < check_out, Reservation.check_out > check_in).limit(1))
    if exclude_reservation_id is not None:
        stmt = stmt.where(Reservation.id != exclude_reservation_id)
    return db.scalar(stmt) is not None


def reservation_rooms(db: Session, reservation_id: int) -> list[int]:
    return db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation_id)).all()


def ensure_stay_children(db: Session, stay: Stay, guest_id: int | None = None) -> None:
    occupant_id = guest_id or stay.guest_id
    if occupant_id and not db.scalar(select(StayOccupant.id).where(StayOccupant.stay_id == stay.id)):
        db.add(StayOccupant(stay_id=stay.id, guest_id=occupant_id, role="primary", is_primary=True, check_in=stay.check_in, check_out=stay.check_out, notes=stay.notes))
    if not db.scalar(select(StayRateSegment.id).where(StayRateSegment.stay_id == stay.id)):
        db.add(StayRateSegment(stay_id=stay.id, from_date=stay.check_in, to_date=stay.check_out, rate=money(stay.agreed_rate + stay.discount_amount), discount_percent=stay.discount_percent, discount_amount=stay.discount_amount, source="reservation", notes=stay.notes))


@router.post("/reservations/workflow", status_code=201)
def create_reservation_workflow(payload: ReservationWorkflowCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    if payload.check_out <= payload.check_in:
        raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if not db.get(Guest, payload.guest_id):
        raise HTTPException(status_code=400, detail="Booking guest does not exist")
    if payload.group_id and not db.get(BookingGroup, payload.group_id):
        raise HTTPException(status_code=400, detail="Booking group does not exist")
    room_ids = [item.room_id for item in payload.rooms]
    if len(room_ids) != len(set(room_ids)):
        raise HTTPException(status_code=400, detail="Duplicate rooms are not allowed")
    rooms = [db.get(Room, room_id) for room_id in room_ids]
    if any(room is None for room in rooms):
        raise HTTPException(status_code=400, detail="One or more rooms do not exist")
    if any(room.status in ("dirty", "out_of_order") for room in rooms):
        raise HTTPException(status_code=409, detail="One or more rooms are not operationally bookable")
    conflicts = [room.number for room in rooms if overlaps(db, room.id, payload.check_in, payload.check_out)]
    if conflicts:
        raise HTTPException(status_code=409, detail=f"Room(s) unavailable for selected dates: {', '.join(conflicts)}")

    stay_days = max(1, (payload.check_out - payload.check_in).days)
    stay_values: list[tuple[ReservationRoomSetup, Decimal, Decimal, int]] = []
    total_estimated = Decimal("0")
    for item in payload.rooms:
        discount, net = discount_values(item.agreed_rate, item.discount_percent, item.fixed_discount)
        occupant_id = item.occupant_guest_id or payload.guest_id
        if not db.get(Guest, occupant_id):
            raise HTTPException(status_code=400, detail=f"Occupant guest {occupant_id} does not exist")
        total_estimated += money(net) * stay_days
        stay_values.append((item, discount, net, occupant_id))
    if payload.deposit_received > total_estimated:
        raise HTTPException(status_code=400, detail="Deposit received exceeds the estimated stay value")

    reservation = Reservation(guest_id=payload.guest_id, check_in=payload.check_in, check_out=payload.check_out, status="reserved", notes=payload.notes)
    db.add(reservation); db.flush()
    folio = Folio(reservation_id=reservation.id, status="open")
    db.add(folio); db.flush()
    stays: list[Stay] = []
    per_room_deposit = money(payload.deposit_received / Decimal(len(room_ids))) if room_ids else Decimal("0")
    for item, discount, net, occupant_id in stay_values:
        stay = Stay(reservation_id=reservation.id, room_id=item.room_id, guest_id=occupant_id, status="reserved", check_in=payload.check_in, check_out=payload.check_out, agreed_rate=net, discount_percent=money(item.discount_percent), discount_amount=discount, payment_due_policy=payload.payment_policy, deposit_required=money(net * stay_days), deposit_received=min(per_room_deposit, money(net * stay_days)), notes=payload.notes)
        db.add(stay); db.flush(); stays.append(stay)
        db.add(StayOccupant(stay_id=stay.id, guest_id=occupant_id, role="primary", is_primary=True, check_in=payload.check_in, check_out=payload.check_out, notes=payload.notes))
        db.add(StayRateSegment(stay_id=stay.id, from_date=payload.check_in, to_date=payload.check_out, rate=money(item.agreed_rate), discount_percent=item.discount_percent, discount_amount=discount, source="reservation", notes=payload.notes))
        db.add(ReservationRoom(reservation_id=reservation.id, room_id=item.room_id))
        room = db.get(Room, item.room_id)
        if room and payload.check_in <= date.today() < payload.check_out:
            room.status = "reserved"
    if payload.group_id:
        db.add(GroupReservation(group_id=payload.group_id, reservation_id=reservation.id, role="member"))
    audit(db, user.id, "create_workflow", reservation.id, {"room_ids": room_ids, "group_id": payload.group_id, "payment_policy": payload.payment_policy, "deposit_received": str(payload.deposit_received)})
    db.commit()
    return {"id": reservation.id, "guest_id": reservation.guest_id, "check_in": reservation.check_in, "check_out": reservation.check_out, "status": reservation.status, "room_ids": room_ids, "folio_id": folio.id, "group_id": payload.group_id}


@router.patch("/reservations/{reservation_id}")
def update_reservation(reservation_id: int, payload: ReservationUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status not in ACTIVE_STATUSES: raise HTTPException(status_code=409, detail="Only active reservations can be modified")
    check_in = payload.check_in or reservation.check_in; check_out = payload.check_out or reservation.check_out
    if check_out <= check_in: raise HTTPException(status_code=400, detail="Check-out must be after check-in")
    if reservation.status == "checked_in" and check_in != reservation.check_in: raise HTTPException(status_code=409, detail="Check-in date cannot be changed after arrival")
    if payload.guest_id is not None and not db.get(Guest, payload.guest_id): raise HTTPException(status_code=400, detail="Guest does not exist")
    old = {"check_in": str(reservation.check_in), "check_out": str(reservation.check_out), "guest_id": reservation.guest_id, "notes": reservation.notes}
    current_rooms = reservation_rooms(db, reservation_id)
    conflicts = [db.get(Room, room_id).number for room_id in current_rooms if db.get(Room, room_id) and overlaps(db, room_id, check_in, check_out, reservation_id)]
    if conflicts: raise HTTPException(status_code=409, detail=f"Updated dates conflict with room(s): {', '.join(conflicts)}")
    reservation.check_in = check_in; reservation.check_out = check_out; reservation.notes = payload.notes
    if payload.guest_id is not None: reservation.guest_id = payload.guest_id
    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation_id)).all():
        stay.check_in = check_in; stay.check_out = check_out
        if payload.guest_id is not None and stay.guest_id == old["guest_id"]:
            stay.guest_id = reservation.guest_id
        ensure_stay_children(db, stay)
    audit(db, user.id, "update", reservation_id, {"from": old, "to": {"check_in": str(check_in), "check_out": str(check_out), "guest_id": reservation.guest_id, "notes": reservation.notes}})
    db.commit()
    return {"id": reservation.id, "check_in": reservation.check_in, "check_out": reservation.check_out, "guest_id": reservation.guest_id, "status": reservation.status, "room_ids": current_rooms, "notes": reservation.notes}


@router.put("/reservations/{reservation_id}/rooms")
def replace_reservation_rooms(reservation_id: int, payload: RoomAssignmentsUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "reserved": raise HTTPException(status_code=409, detail="Room allocation can only be changed before check-in")
    old_ids = reservation_rooms(db, reservation_id)
    if len(payload.room_ids) != len(set(payload.room_ids)): raise HTTPException(status_code=400, detail="Duplicate room IDs are not allowed")
    rooms = [db.get(Room, room_id) for room_id in payload.room_ids]
    if any(room is None for room in rooms): raise HTTPException(status_code=400, detail="One or more rooms do not exist")
    if any(room.status in ("dirty", "out_of_order") for room in rooms): raise HTTPException(status_code=409, detail="One or more rooms are not operationally bookable")
    conflicts = [room.number for room in rooms if overlaps(db, room.id, reservation.check_in, reservation.check_out, reservation_id)]
    if conflicts: raise HTTPException(status_code=409, detail=f"Room(s) unavailable for dates: {', '.join(conflicts)}")
    for link in db.scalars(select(ReservationRoom).where(ReservationRoom.reservation_id == reservation_id)).all():
        if link.room_id not in payload.room_ids: db.delete(link)
    for room_id in payload.room_ids:
        if room_id not in old_ids: db.add(ReservationRoom(reservation_id=reservation_id, room_id=room_id))
    db.flush()
    audit(db, user.id, "room_allocation_update", reservation_id, {"from_room_ids": old_ids, "to_room_ids": payload.room_ids})
    db.commit()
    return {"reservation_id": reservation_id, "room_ids": payload.room_ids}


@router.post("/reservations/{reservation_id}/extend")
def extend_stay(reservation_id: int, payload: ExtendStay, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in": raise HTTPException(status_code=409, detail="Only an in-house stay can be extended")
    if payload.new_check_out <= reservation.check_in or payload.new_check_out <= date.today(): raise HTTPException(status_code=400, detail="New check-out date is invalid")
    if payload.new_check_out <= reservation.check_out: raise HTTPException(status_code=400, detail="Use a later date to extend the stay")
    room_ids = reservation_rooms(db, reservation_id)
    conflicts = [db.get(Room, room_id).number for room_id in room_ids if db.get(Room, room_id) and overlaps(db, room_id, reservation.check_out, payload.new_check_out, reservation_id)]
    if conflicts: raise HTTPException(status_code=409, detail=f"Extension conflicts with room(s): {', '.join(conflicts)}")
    old_checkout = reservation.check_out; reservation.check_out = payload.new_check_out
    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation_id)).all():
        stay.check_out = payload.new_check_out; ensure_stay_children(db, stay)
    audit(db, user.id, "extend", reservation_id, {"from_check_out": str(old_checkout), "to_check_out": str(payload.new_check_out), "room_ids": room_ids})
    db.commit()
    return {"reservation_id": reservation_id, "old_check_out": old_checkout, "new_check_out": reservation.check_out, "room_ids": room_ids}


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int, payload: ActionReason | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status not in CANCELLABLE_STATUSES: raise HTTPException(status_code=409, detail="Only reservations awaiting check-in can be cancelled")
    reservation.status = "cancelled"; room_ids = reservation_rooms(db, reservation_id)
    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and room.status == "reserved": room.status = "available"
    audit(db, user.id, "cancel", reservation_id, {"reason": payload.reason if payload else None, "room_ids": room_ids, "cancelled_at": datetime.utcnow().isoformat()})
    db.commit(); return {"reservation_id": reservation_id, "status": reservation.status, "room_ids": room_ids}


@router.post("/reservations/{reservation_id}/no-show")
def mark_no_show(reservation_id: int, payload: ActionReason | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "reserved": raise HTTPException(status_code=409, detail="Only a reserved arrival can be marked no-show")
    if reservation.check_in > date.today(): raise HTTPException(status_code=409, detail="A future reservation cannot be marked no-show")
    reservation.status = "no_show"; room_ids = reservation_rooms(db, reservation_id)
    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and room.status == "reserved": room.status = "available"
    audit(db, user.id, "no_show", reservation_id, {"reason": payload.reason if payload else None, "room_ids": room_ids, "marked_at": datetime.utcnow().isoformat()})
    db.commit(); return {"reservation_id": reservation_id, "status": reservation.status, "room_ids": room_ids}


@router.get("/reservations/{reservation_id}/availability-check")
def reservation_availability_check(reservation_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    room_ids = reservation_rooms(db, reservation_id); conflicts = []
    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and overlaps(db, room_id, reservation.check_in, reservation.check_out, reservation_id): conflicts.append(room.number)
    return {"reservation_id": reservation_id, "check_in": reservation.check_in, "check_out": reservation.check_out, "available": not conflicts, "conflicts": conflicts}