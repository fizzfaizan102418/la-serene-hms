from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import (
    AuditLog,
    DepositTransaction,
    Folio,
    Guest,
    Reservation,
    ReservationRoom,
    Room,
    RoomMove,
    StayOccupant,
    StayRateSegment,
    User,
)
from .pms_core import Stay

router = APIRouter(prefix="/api", tags=["phase-a-workflows"])
ACTIVE = ("reserved", "checked_in")
MONEY = Decimal("0.01")


class OccupantCreate(BaseModel):
    guest_id: int
    role: str = Field(default="occupant", max_length=30)
    is_primary: bool = False
    check_in: date | None = None
    check_out: date | None = None
    notes: str | None = None


class DepositRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: str | None = Field(default=None, max_length=30)
    reference: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class DepositTransfer(BaseModel):
    target_stay_id: int
    amount: Decimal = Field(gt=0)
    reference: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class DepositApply(BaseModel):
    amount: Decimal = Field(gt=0)
    notes: str | None = None


class MoveRoomRequest(BaseModel):
    to_room_id: int
    reason: str | None = Field(default=None, max_length=300)


class ReservationSplitRequest(BaseModel):
    to_date: date
    room_stay_ids: list[int] = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=300)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def stay_or_404(db: Session, stay_id: int) -> Stay:
    stay = db.get(Stay, stay_id)
    if not stay:
        raise HTTPException(status_code=404, detail="Stay not found")
    return stay


def deposit_balance(db: Session, stay_id: int) -> Decimal:
    rows = db.scalars(select(DepositTransaction).where(DepositTransaction.stay_id == stay_id)).all()
    balance = Decimal("0")
    for row in rows:
        if row.transaction_type in {"received", "transfer_in"}:
            balance += Decimal(row.amount)
        elif row.transaction_type in {"refund", "transfer_out", "applied"}:
            balance -= Decimal(row.amount)
    return balance.quantize(MONEY)


def room_available(db: Session, room_id: int, stay: Stay) -> bool:
    room = db.get(Room, room_id)
    if not room or room.status in {"dirty", "out_of_order", "occupied", "reserved"}:
        return False
    conflict = db.scalar(
        select(Stay.id).where(
            Stay.room_id == room_id,
            Stay.id != stay.id,
            Stay.status.in_(ACTIVE),
            Stay.check_in < stay.check_out,
            Stay.check_out > stay.check_in,
        ).limit(1)
    )
    return conflict is None


def add_occupant_internal(
    db: Session,
    stay: Stay,
    payload: OccupantCreate,
    user_id: int,
) -> StayOccupant:
    guest = db.get(Guest, payload.guest_id)
    if not guest:
        raise HTTPException(status_code=400, detail="Guest does not exist")
    check_in = payload.check_in or stay.check_in
    check_out = payload.check_out or stay.check_out
    if check_in < stay.check_in or check_out > stay.check_out or check_out <= check_in:
        raise HTTPException(status_code=400, detail="Occupant dates must be within the stay")
    existing = db.scalar(
        select(StayOccupant.id).where(
            StayOccupant.stay_id == stay.id,
            StayOccupant.guest_id == payload.guest_id,
            StayOccupant.check_out > check_in,
            StayOccupant.check_in < check_out,
        ).limit(1)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Guest is already an occupant for this period")
    if payload.is_primary:
        for other in db.scalars(select(StayOccupant).where(StayOccupant.stay_id == stay.id)).all():
            other.is_primary = False
    has_primary = db.scalar(select(StayOccupant.id).where(StayOccupant.stay_id == stay.id, StayOccupant.is_primary.is_(True)).limit(1))
    occupant = StayOccupant(
        stay_id=stay.id,
        guest_id=payload.guest_id,
        role=payload.role,
        is_primary=payload.is_primary or has_primary is None,
        check_in=check_in,
        check_out=check_out,
        notes=payload.notes,
    )
    db.add(occupant)
    db.flush()
    audit(db, user_id, "occupant_add", "stay_occupant", occupant.id, {"stay_id": stay.id, "guest_id": occupant.guest_id, "is_primary": occupant.is_primary})
    return occupant


@router.post("/stays/{stay_id}/occupants", status_code=201)
def add_stay_occupant(stay_id: int, payload: OccupantCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    occupant = add_occupant_internal(db, stay, payload, user.id)
    db.commit()
    return {"id": occupant.id, "stay_id": occupant.stay_id, "guest_id": occupant.guest_id, "role": occupant.role, "is_primary": occupant.is_primary, "check_in": occupant.check_in, "check_out": occupant.check_out, "notes": occupant.notes}


@router.get("/stays/{stay_id}/deposits")
def list_stay_deposits(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    stay_or_404(db, stay_id)
    rows = db.scalars(select(DepositTransaction).where(DepositTransaction.stay_id == stay_id).order_by(DepositTransaction.id)).all()
    return {"stay_id": stay_id, "balance": deposit_balance(db, stay_id), "transactions": [
        {"id": row.id, "type": row.transaction_type, "amount": row.amount, "folio_id": row.folio_id, "payment_method": row.payment_method, "reference": row.reference, "notes": row.notes, "created_at": row.created_at, "created_by": row.created_by}
        for row in rows
    ]}


@router.post("/stays/{stay_id}/deposits/receive", status_code=201)
def receive_deposit(stay_id: int, payload: DepositRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    transaction = DepositTransaction(
        stay_id=stay.id,
        transaction_type="received",
        amount=payload.amount.quantize(MONEY),
        payment_method=payload.payment_method,
        reference=payload.reference,
        notes=payload.notes,
        created_by=user.id,
    )
    db.add(transaction)
    db.flush()
    audit(db, user.id, "deposit_receive", "deposit_transaction", transaction.id, {"stay_id": stay.id, "amount": str(transaction.amount)})
    db.commit()
    return {"id": transaction.id, "stay_id": stay.id, "balance": deposit_balance(db, stay.id)}


@router.post("/stays/{stay_id}/deposits/refund", status_code=201)
def refund_deposit(stay_id: int, payload: DepositRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    amount = payload.amount.quantize(MONEY)
    balance = deposit_balance(db, stay.id)
    if amount > balance:
        raise HTTPException(status_code=409, detail=f"Deposit refund exceeds available balance of {balance}")
    transaction = DepositTransaction(
        stay_id=stay.id,
        transaction_type="refund",
        amount=amount,
        payment_method=payload.payment_method,
        reference=payload.reference,
        notes=payload.notes,
        created_by=user.id,
    )
    db.add(transaction)
    db.flush()
    audit(db, user.id, "deposit_refund", "deposit_transaction", transaction.id, {"stay_id": stay.id, "amount": str(amount)})
    db.commit()
    return {"id": transaction.id, "stay_id": stay.id, "balance": deposit_balance(db, stay.id)}


@router.post("/stays/{stay_id}/deposits/transfer", status_code=201)
def transfer_deposit(stay_id: int, payload: DepositTransfer, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    source = stay_or_404(db, stay_id)
    target = stay_or_404(db, payload.target_stay_id)
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Deposit transfer requires a different target stay")
    if source.reservation_id == target.reservation_id:
        raise HTTPException(status_code=400, detail="Deposit transfer target must belong to a different reservation")
    amount = payload.amount.quantize(MONEY)
    if amount > deposit_balance(db, source.id):
        raise HTTPException(status_code=409, detail="Insufficient deposit balance")
    outbound = DepositTransaction(stay_id=source.id, transaction_type="transfer_out", amount=amount, reference=payload.reference, notes=payload.notes, created_by=user.id)
    inbound = DepositTransaction(stay_id=target.id, transaction_type="transfer_in", amount=amount, reference=payload.reference, notes=payload.notes, created_by=user.id)
    db.add_all([outbound, inbound])
    db.flush()
    audit(db, user.id, "deposit_transfer", "deposit_transaction", outbound.id, {"source_stay_id": source.id, "target_stay_id": target.id, "amount": str(amount), "inbound_id": inbound.id})
    db.commit()
    return {"source_stay_id": source.id, "target_stay_id": target.id, "transferred": amount, "source_balance": deposit_balance(db, source.id), "target_balance": deposit_balance(db, target.id)}


@router.post("/stays/{stay_id}/deposits/apply", status_code=201)
def apply_deposit(stay_id: int, payload: DepositApply, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    folio_id = db.scalar(select(Folio.id).where(Folio.reservation_id == stay.reservation_id).limit(1))
    if folio_id is None:
        raise HTTPException(status_code=409, detail="No folio exists for this reservation")
    amount = payload.amount.quantize(MONEY)
    if amount > deposit_balance(db, stay.id):
        raise HTTPException(status_code=409, detail="Deposit application exceeds available balance")
    transaction = DepositTransaction(stay_id=stay.id, folio_id=folio_id, transaction_type="applied", amount=amount, notes=payload.notes, created_by=user.id)
    db.add(transaction)
    db.flush()
    audit(db, user.id, "deposit_apply", "deposit_transaction", transaction.id, {"stay_id": stay.id, "folio_id": folio_id, "amount": str(amount)})
    db.commit()
    return {"id": transaction.id, "stay_id": stay.id, "folio_id": folio_id, "applied": amount, "balance": deposit_balance(db, stay.id)}


@router.post("/stays/{stay_id}/move-room", status_code=201)
def move_checked_in_stay(stay_id: int, payload: MoveRoomRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    if stay.status != "checked_in":
        raise HTTPException(status_code=409, detail="Only checked-in stays can use room move")
    source = db.get(Room, stay.room_id)
    target = db.get(Room, payload.to_room_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or destination room not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Destination room must be different")
    if not room_available(db, target.id, stay):
        raise HTTPException(status_code=409, detail="Destination room is not available")
    move = RoomMove(stay_id=stay.id, from_room_id=source.id, to_room_id=target.id, effective_at=datetime.utcnow(), reason=payload.reason, created_by=user.id)
    db.add(move)
    stay.room_id = target.id
    source.status = "dirty"
    target.status = "occupied"
    link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == stay.reservation_id, ReservationRoom.room_id == source.id))
    if link:
        db.delete(link)
    db.add(ReservationRoom(reservation_id=stay.reservation_id, room_id=target.id))
    db.flush()
    audit(db, user.id, "room_move", "stay", stay.id, {"from_room_id": source.id, "to_room_id": target.id, "reason": payload.reason, "room_move_id": move.id})
    db.commit()
    return {"stay_id": stay.id, "from_room_id": source.id, "to_room_id": target.id, "room_move_id": move.id, "status": stay.status}


@router.post("/reservations/{reservation_id}/split", status_code=201)
def split_reservation(reservation_id: int, payload: ReservationSplitRequest, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    source = db.get(Reservation, reservation_id)
    if not source:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if source.status != "reserved":
        raise HTTPException(status_code=409, detail="Only reserved reservations can be split")
    if payload.to_date <= source.check_in or payload.to_date >= source.check_out:
        raise HTTPException(status_code=400, detail="Split date must be inside the reservation dates")
    stay_ids = sorted(set(payload.room_stay_ids))
    if len(stay_ids) != len(payload.room_stay_ids):
        raise HTTPException(status_code=400, detail="Duplicate room stay ids are not allowed")
    stays = [db.get(Stay, stay_id) for stay_id in stay_ids]
    if any(stay is None for stay in stays):
        raise HTTPException(status_code=404, detail="One or more room stays were not found")
    selected = [stay for stay in stays if stay is not None]
    if any(stay.reservation_id != source.id for stay in selected):
        raise HTTPException(status_code=400, detail="All selected stays must belong to the source reservation")
    if any(stay.status != "reserved" for stay in selected):
        raise HTTPException(status_code=409, detail="Only reserved room stays can be split")
    if any(not (stay.check_in < payload.to_date < stay.check_out) for stay in selected):
        raise HTTPException(status_code=400, detail="Split date must be inside every selected room stay")

    new_reservation = Reservation(
        guest_id=source.guest_id,
        check_in=payload.to_date,
        check_out=source.check_out,
        status="reserved",
        notes=(f"Split from reservation #{source.id}. {payload.reason}" if payload.reason else f"Split from reservation #{source.id}."),
    )
    db.add(new_reservation)
    db.flush()
    db.add(Folio(reservation_id=new_reservation.id, status="open"))

    new_stays = []
    for stay in selected:
        old_check_out = stay.check_out
        stay.check_out = payload.to_date
        link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == source.id, ReservationRoom.room_id == stay.room_id))
        if link:
            db.delete(link)
        db.add(ReservationRoom(reservation_id=new_reservation.id, room_id=stay.room_id))
        new_stay = Stay(
            reservation_id=new_reservation.id,
            room_id=stay.room_id,
            guest_id=stay.guest_id,
            status="reserved",
            check_in=payload.to_date,
            check_out=old_check_out,
            agreed_rate=stay.agreed_rate,
            discount_percent=stay.discount_percent,
            discount_amount=stay.discount_amount,
            payment_due_policy=stay.payment_due_policy,
            deposit_required=stay.deposit_required,
            deposit_received=Decimal("0"),
            notes=stay.notes,
        )
        db.add(new_stay)
        db.flush()
        for segment in db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == stay.id, StayRateSegment.from_date < old_check_out, StayRateSegment.to_date > payload.to_date)).all():
            db.add(StayRateSegment(
                stay_id=new_stay.id,
                from_date=max(segment.from_date, payload.to_date),
                to_date=min(segment.to_date, old_check_out),
                rate=segment.rate,
                discount_percent=segment.discount_percent,
                discount_amount=segment.discount_amount,
                rate_plan=segment.rate_plan,
                source="reservation_split",
                notes=segment.notes,
            ))
        for occupant in db.scalars(select(StayOccupant).where(StayOccupant.stay_id == stay.id)).all():
            occ_in = max(occupant.check_in or stay.check_in, payload.to_date)
            occ_out = min(occupant.check_out or old_check_out, old_check_out)
            if occ_in < occ_out:
                db.add(StayOccupant(stay_id=new_stay.id, guest_id=occupant.guest_id, role=occupant.role, is_primary=occupant.is_primary, check_in=occ_in, check_out=occ_out, notes=occupant.notes))
        new_stays.append(new_stay)

    split = ReservationSplit(source_reservation_id=source.id, new_reservation_id=new_reservation.id, reason=payload.reason, split_check_in=payload.to_date, split_check_out=source.check_out, created_by=user.id)
    db.add(split)
    audit(db, user.id, "reservation_split", "reservation", source.id, {"new_reservation_id": new_reservation.id, "stay_ids": stay_ids, "split_date": str(payload.to_date), "reason": payload.reason})
    db.commit()
    return {"source_reservation_id": source.id, "new_reservation_id": new_reservation.id, "split_date": payload.to_date, "stays": [{"id": stay.id, "room_id": stay.room_id, "reservation_id": stay.reservation_id, "check_in": stay.check_in, "check_out": stay.check_out} for stay in new_stays]}
