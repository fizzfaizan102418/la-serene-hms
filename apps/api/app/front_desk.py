from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .business_date import get_current_business_date
from .financial_authority import folio_ledger_summary
from .ledger import post_deposit_received
from .models import AuditLog, Folio, FolioItem, Guest, Reservation, ReservationRoom, Room, RoomType, User
from .pms_core import Stay

router = APIRouter(tags=["front-desk-2"])
MONEY = Decimal("0.01")


class WalkInRoom(BaseModel):
    room_id: int
    occupant_guest_id: int | None = None
    agreed_rate: Decimal = Field(ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    fixed_discount: Decimal = Field(default=Decimal("0"), ge=0)


class WalkInCreate(BaseModel):
    guest_id: int
    rooms: list[WalkInRoom] = Field(min_length=1)
    check_out: date
    payment_policy: str = Field(default="at_checkout", pattern="^(at_checkin|at_checkout|partial)$")
    deposit_received: Decimal = Field(default=Decimal("0"), ge=0)
    deposit_method: str = Field(default="cash", max_length=30)
    notes: str | None = Field(default=None, max_length=1000)


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def folio_balance(db: Session, folio: Folio) -> Decimal:
    return folio_ledger_summary(db, folio.id).balance


def available_for_walk_in(db: Session, room_id: int, business_date: date) -> bool:
    room = db.get(Room, room_id)
    if not room or room.status in ("dirty", "out_of_order", "occupied", "reserved"):
        return False
    conflict = db.scalar(select(ReservationRoom.reservation_id).join(Reservation, Reservation.id == ReservationRoom.reservation_id).where(ReservationRoom.room_id == room_id, Reservation.status.in_(("reserved", "checked_in")), Reservation.check_in <= business_date, Reservation.check_out > business_date).limit(1))
    return conflict is None


@router.get("/front-desk/search")
def universal_search(q: str = Query(min_length=1, max_length=160), db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    term = q.strip()
    pattern = f"%{term}%"
    results: list[dict] = []
    for guest in db.scalars(select(Guest).where(or_(Guest.full_name.ilike(pattern), Guest.phone.ilike(pattern), Guest.email.ilike(pattern))).order_by(Guest.full_name).limit(20)).all():
        results.append({"type": "guest", "id": guest.id, "label": guest.full_name, "secondary": guest.phone or guest.email or "", "guest_id": guest.id})
    for reservation, guest_name in db.execute(select(Reservation, Guest.full_name).join(Guest, Guest.id == Reservation.guest_id).where(or_(Guest.full_name.ilike(pattern), Reservation.notes.ilike(pattern))).order_by(Reservation.id.desc()).limit(20)).all():
        results.append({"type": "reservation", "id": reservation.id, "label": f"Reservation #{reservation.id}", "secondary": f"{guest_name} · {reservation.check_in} → {reservation.check_out} · {reservation.status}", "guest_id": reservation.guest_id})
    for room in db.scalars(select(Room).where(Room.number.ilike(pattern)).order_by(Room.number).limit(20)).all():
        results.append({"type": "room", "id": room.id, "label": f"Room {room.number}", "secondary": room.status, "room_id": room.id})
    for folio, reservation_id, guest_name in db.execute(select(Folio, Reservation.id, Guest.full_name).join(Reservation, Reservation.id == Folio.reservation_id).join(Guest, Guest.id == Reservation.guest_id).where(Guest.full_name.ilike(pattern)).order_by(Folio.id.desc()).limit(20)).all():
        results.append({"type": "folio", "id": folio.id, "label": f"Folio #{folio.id}", "secondary": f"{guest_name} · balance PKR {folio_balance(db, folio)}", "folio_id": folio.id, "reservation_id": reservation_id})
    return {"query": term, "results": results[:60]}


@router.get("/front-desk/room-rack")
def room_rack(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    return [{"room_id": room.id, "room_number": room.number, "room_type": room_type, "status": room.status} for room, room_type in db.execute(select(Room, RoomType.name).join(RoomType, RoomType.id == Room.room_type_id).order_by(Room.number)).all()]


@router.post("/front-desk/walk-ins", status_code=201)
def create_walk_in(payload: WalkInCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    business_date = get_current_business_date(db, fallback_to_today=True)
    if payload.check_out <= business_date:
        raise HTTPException(status_code=400, detail="Walk-in check-out must be after the current business date")
    if not db.get(Guest, payload.guest_id):
        raise HTTPException(status_code=400, detail="Guest does not exist")
    room_ids = [item.room_id for item in payload.rooms]
    if len(room_ids) != len(set(room_ids)):
        raise HTTPException(status_code=400, detail="Duplicate rooms are not allowed")
    nights = (payload.check_out - business_date).days
    prepared: list[tuple[WalkInRoom, int, Decimal, Decimal, Decimal]] = []
    estimated_total = Decimal("0.00")
    for item in payload.rooms:
        if not available_for_walk_in(db, item.room_id, business_date):
            room = db.get(Room, item.room_id)
            raise HTTPException(status_code=409, detail=f"Room {room.number if room else item.room_id} is not available for walk-in")
        occupant_id = item.occupant_guest_id or payload.guest_id
        if not db.get(Guest, occupant_id):
            raise HTTPException(status_code=400, detail=f"Occupant guest {occupant_id} does not exist")
        if item.discount_percent and item.fixed_discount:
            raise HTTPException(status_code=400, detail="Use either percentage or fixed discount, not both")
        rate = money(item.agreed_rate)
        discount = money(rate * item.discount_percent / Decimal("100")) if item.discount_percent else min(money(item.fixed_discount), rate)
        net = money(rate - discount)
        estimated_total += net * nights
        prepared.append((item, occupant_id, rate, discount, net))
    if payload.deposit_received > estimated_total:
        raise HTTPException(status_code=400, detail="Deposit received exceeds estimated stay value")
    reservation = Reservation(guest_id=payload.guest_id, check_in=business_date, check_out=payload.check_out, status="checked_in", notes=payload.notes)
    db.add(reservation); db.flush()
    folio = Folio(reservation_id=reservation.id, status="open")
    db.add(folio); db.flush()
    per_room_deposit = money(payload.deposit_received / Decimal(len(prepared)))
    stays: list[Stay] = []
    for item, occupant_id, rate, discount, net in prepared:
        db.add(ReservationRoom(reservation_id=reservation.id, room_id=item.room_id))
        stay = Stay(reservation_id=reservation.id, room_id=item.room_id, guest_id=occupant_id, status="checked_in", check_in=business_date, check_out=payload.check_out, actual_check_in=datetime.utcnow(), agreed_rate=net, discount_percent=item.discount_percent, discount_amount=discount, payment_due_policy=payload.payment_policy, deposit_required=net * nights, deposit_received=min(per_room_deposit, money(net * nights)), notes=payload.notes)
        db.add(stay); db.flush(); stays.append(stay)
        room = db.get(Room, item.room_id)
        if room: room.status = "occupied"
        assigned_deposit = min(per_room_deposit, money(net * nights))
        if assigned_deposit > 0:
            deposit = __import__("app.models", fromlist=["DepositTransaction"]).DepositTransaction(stay_id=stay.id, folio_id=folio.id, transaction_type="received", amount=assigned_deposit, payment_method=payload.deposit_method, reference=f"WALKIN-{reservation.id}-{stay.id}", notes="Walk-in deposit received", created_by=user.id)
            db.add(deposit); db.flush()
            post_deposit_received(db, stay_id=stay.id, folio_id=folio.id, reservation_id=reservation.id, deposit_id=deposit.id, amount=assigned_deposit, method=payload.deposit_method, created_by=user.id)
    audit(db, user.id, "walk_in_check_in", "reservation", reservation.id, {"room_ids": room_ids, "guest_id": payload.guest_id, "deposit_received": str(payload.deposit_received), "deposit_method": payload.deposit_method})
    db.commit()
    return {"reservation_id": reservation.id, "folio_id": folio.id, "stay_ids": [stay.id for stay in stays], "room_ids": room_ids, "guest_id": payload.guest_id, "check_in": business_date, "check_out": payload.check_out, "status": reservation.status, "estimated_total": money(estimated_total), "deposit_received": money(payload.deposit_received), "deposit_method": payload.deposit_method}


@router.post("/reservations/{reservation_id}/checkout", response_model=dict)
def atomic_checkout(reservation_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in":
        raise HTTPException(status_code=409, detail="Reservation is not checked in")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == reservation.id).order_by(Folio.id).limit(1))
    if not folio:
        raise HTTPException(status_code=409, detail="Reservation has no folio")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is already closed")
    balance = folio_balance(db, folio)
    if balance != 0:
        raise HTTPException(status_code=409, detail=f"Guest must settle the folio before checkout; outstanding balance is {balance}")
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation.id)).all()
    checkout_at = datetime.utcnow()
    folio.status = "closed"
    reservation.status = "checked_out"
    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation.id)).all():
        stay.status = "completed"
        stay.actual_check_out = stay.actual_check_out or checkout_at
    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and room.status == "occupied":
            room.status = "dirty"
    audit(db, user.id, "atomic_checkout", "reservation", reservation.id, {"folio_id": folio.id, "room_ids": room_ids, "checked_out_at": checkout_at.isoformat()})
    db.commit()
    return {"reservation_id": reservation.id, "folio_id": folio.id, "status": reservation.status, "folio_status": folio.status, "room_ids": room_ids, "room_status": "dirty"}
