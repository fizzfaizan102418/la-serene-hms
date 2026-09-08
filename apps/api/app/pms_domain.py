from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import (
    AuditLog,
    BusinessDateState,
    DepositTransaction,
    Guest,
    Folio,
    FolioItem,
    Reservation,
    ReservationRoom,
    ReservationSplit,
    Room,
    RoomMove,
    StayOccupant,
    StayRateSegment,
    User,
)
from .pms_core import Stay

router = APIRouter(prefix="/api", tags=["pms-domain"])
MONEY = Decimal("0.01")


def money(value: Decimal | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict | None = None) -> None:
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details or {})))


def ensure_default_rate_segment(db: Session, stay: Stay) -> list[StayRateSegment]:
    segments = db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == stay.id).order_by(StayRateSegment.from_date, StayRateSegment.id)).all()
    if segments:
        return segments
    segment = StayRateSegment(
        stay_id=stay.id,
        from_date=stay.check_in,
        to_date=stay.check_out,
        rate=money(stay.agreed_rate + stay.discount_amount),
        discount_percent=stay.discount_percent,
        discount_amount=stay.discount_amount,
        source="migration",
    )
    db.add(segment)
    db.flush()
    return [segment]


class OccupantCreate(BaseModel):
    guest_id: int
    role: str = Field(default="occupant", max_length=30)
    is_primary: bool = False
    check_in: date | None = None
    check_out: date | None = None
    notes: str | None = None


class RateSegmentCreate(BaseModel):
    from_date: date
    to_date: date
    rate: Decimal = Field(ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    rate_plan: str | None = Field(default=None, max_length=80)
    source: str = Field(default="manual", max_length=30)
    notes: str | None = None


class DepositCreate(BaseModel):
    transaction_type: str = Field(pattern="^(received|applied|refunded|adjusted)$")
    amount: Decimal = Field(gt=0)
    payment_method: str | None = Field(default=None, max_length=30)
    reference: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class RoomMoveCreate(BaseModel):
    to_room_id: int
    reason: str | None = Field(default=None, max_length=300)


class ReservationSplitCreate(BaseModel):
    room_ids: list[int] = Field(min_length=1)
    new_guest_id: int | None = None
    split_check_in: date | None = None
    split_check_out: date | None = None
    reason: str | None = Field(default=None, max_length=300)


@router.get("/business-date")
def get_business_date(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    if state is None:
        state = BusinessDateState(id=1, current_business_date=date.today(), opened_at=datetime.utcnow())
        db.add(state); db.commit(); db.refresh(state)
    return {"business_date": state.current_business_date, "opened_at": state.opened_at, "last_closed_at": state.last_closed_at}


@router.post("/stays/{stay_id}/occupants", status_code=201)
def add_stay_occupant(stay_id: int, payload: OccupantCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = db.get(Stay, stay_id)
    if not stay: raise HTTPException(status_code=404, detail="Stay not found")
    if not db.get(Guest, payload.guest_id): raise HTTPException(status_code=400, detail="Guest does not exist")
    check_in = payload.check_in or stay.check_in; check_out = payload.check_out or stay.check_out
    if check_out <= check_in or check_in < stay.check_in or check_out > stay.check_out:
        raise HTTPException(status_code=400, detail="Occupant dates must be within the stay")
    if payload.is_primary:
        for occupant in db.scalars(select(StayOccupant).where(StayOccupant.stay_id == stay_id)).all():
            occupant.is_primary = False
    occupant = StayOccupant(stay_id=stay_id, guest_id=payload.guest_id, role=payload.role, is_primary=payload.is_primary, check_in=check_in, check_out=check_out, notes=payload.notes)
    db.add(occupant); db.flush(); audit(db, user.id, "add", "stay_occupant", occupant.id, {"stay_id": stay_id, "guest_id": payload.guest_id})
    db.commit(); return {"id": occupant.id, "stay_id": occupant.stay_id, "guest_id": occupant.guest_id, "role": occupant.role, "is_primary": occupant.is_primary, "check_in": occupant.check_in, "check_out": occupant.check_out, "notes": occupant.notes}


@router.get("/stays/{stay_id}/occupants")
def list_stay_occupants(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    if not db.get(Stay, stay_id): raise HTTPException(status_code=404, detail="Stay not found")
    rows = db.execute(select(StayOccupant, Guest.full_name).join(Guest, Guest.id == StayOccupant.guest_id).where(StayOccupant.stay_id == stay_id).order_by(StayOccupant.is_primary.desc(), StayOccupant.id)).all()
    return [{"id": o.id, "stay_id": o.stay_id, "guest_id": o.guest_id, "guest_name": name, "role": o.role, "is_primary": o.is_primary, "check_in": o.check_in, "check_out": o.check_out, "notes": o.notes} for o, name in rows]


@router.get("/stays/{stay_id}/rate-segments")
def list_rate_segments(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    stay = db.get(Stay, stay_id)
    if not stay: raise HTTPException(status_code=404, detail="Stay not found")
    segments = ensure_default_rate_segment(db, stay); db.commit()
    return [{"id": s.id, "stay_id": s.stay_id, "from_date": s.from_date, "to_date": s.to_date, "rate": s.rate, "discount_percent": s.discount_percent, "discount_amount": s.discount_amount, "rate_plan": s.rate_plan, "source": s.source, "notes": s.notes} for s in segments]


@router.post("/stays/{stay_id}/rate-segments", status_code=201)
def create_rate_segment(stay_id: int, payload: RateSegmentCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = db.get(Stay, stay_id)
    if not stay: raise HTTPException(status_code=404, detail="Stay not found")
    if payload.to_date <= payload.from_date or payload.from_date < stay.check_in or payload.to_date > stay.check_out:
        raise HTTPException(status_code=400, detail="Rate segment dates must be within the stay")
    if payload.discount_percent and payload.discount_amount:
        raise HTTPException(status_code=400, detail="Use either percentage or fixed discount")
    if db.scalar(select(StayRateSegment.id).where(StayRateSegment.stay_id == stay_id, StayRateSegment.from_date < payload.to_date, StayRateSegment.to_date > payload.from_date)):
        raise HTTPException(status_code=409, detail="Rate segment overlaps an existing segment")
    discount = money(payload.rate * payload.discount_percent / Decimal("100")) if payload.discount_percent else money(payload.discount_amount)
    discount = min(discount, money(payload.rate))
    segment = StayRateSegment(stay_id=stay_id, from_date=payload.from_date, to_date=payload.to_date, rate=money(payload.rate), discount_percent=payload.discount_percent, discount_amount=discount, rate_plan=payload.rate_plan, source=payload.source, notes=payload.notes)
    db.add(segment); db.flush(); audit(db, user.id, "create", "stay_rate_segment", segment.id, {"stay_id": stay_id})
    db.commit(); return {"id": segment.id, "stay_id": segment.stay_id, "from_date": segment.from_date, "to_date": segment.to_date, "rate": segment.rate, "discount_percent": segment.discount_percent, "discount_amount": segment.discount_amount, "rate_plan": segment.rate_plan, "source": segment.source, "notes": segment.notes}


@router.get("/stays/{stay_id}/deposits")
def list_deposits(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    if not db.get(Stay, stay_id): raise HTTPException(status_code=404, detail="Stay not found")
    rows = db.scalars(select(DepositTransaction).where(DepositTransaction.stay_id == stay_id).order_by(DepositTransaction.created_at, DepositTransaction.id)).all()
    balance = Decimal("0.00")
    result = []
    for item in rows:
        signed = item.amount if item.transaction_type in {"received", "adjusted"} else -item.amount
        balance += signed
        result.append({"id": item.id, "stay_id": item.stay_id, "folio_id": item.folio_id, "transaction_type": item.transaction_type, "amount": item.amount, "payment_method": item.payment_method, "reference": item.reference, "notes": item.notes, "created_at": item.created_at})
    return {"balance": money(balance), "transactions": result}


@router.post("/stays/{stay_id}/deposits", status_code=201)
def create_deposit(stay_id: int, payload: DepositCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = db.get(Stay, stay_id)
    if not stay: raise HTTPException(status_code=404, detail="Stay not found")
    current = Decimal("0.00")
    for item in db.scalars(select(DepositTransaction).where(DepositTransaction.stay_id == stay_id)).all():
        current += item.amount if item.transaction_type in {"received", "adjusted"} else -item.amount
    signed = payload.amount if payload.transaction_type in {"received", "adjusted"} else -payload.amount
    new_balance = money(current + signed)
    if new_balance < 0: raise HTTPException(status_code=409, detail="Deposit transaction exceeds the available deposit balance")
    if payload.transaction_type == "received" and new_balance > stay.deposit_required and stay.deposit_required > 0:
        raise HTTPException(status_code=409, detail="Deposit received exceeds the required deposit")
    tx = DepositTransaction(stay_id=stay_id, transaction_type=payload.transaction_type, amount=money(payload.amount), payment_method=payload.payment_method, reference=payload.reference, notes=payload.notes, created_by=user.id)
    db.add(tx); db.flush(); stay.deposit_received = new_balance
    audit(db, user.id, "deposit", "stay", stay_id, {"transaction_type": payload.transaction_type, "amount": str(payload.amount), "new_balance": str(new_balance)})
    db.commit(); return {"id": tx.id, "stay_id": stay_id, "transaction_type": tx.transaction_type, "amount": tx.amount, "balance": new_balance}


@router.post("/stays/{stay_id}/move", status_code=201)
def move_stay_room(stay_id: int, payload: RoomMoveCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = db.get(Stay, stay_id)
    if not stay: raise HTTPException(status_code=404, detail="Stay not found")
    if stay.status != "checked_in": raise HTTPException(status_code=409, detail="Room move requires a checked-in stay")
    target = db.get(Room, payload.to_room_id); source = db.get(Room, stay.room_id)
    if not target or not source: raise HTTPException(status_code=404, detail="Source or destination room not found")
    if target.id == source.id: raise HTTPException(status_code=400, detail="Destination room must be different")
    if target.status != "available": raise HTTPException(status_code=409, detail="Destination room is not available")
    move = RoomMove(stay_id=stay.id, from_room_id=source.id, to_room_id=target.id, reason=payload.reason, created_by=user.id)
    db.add(move)
    link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == stay.reservation_id, ReservationRoom.room_id == source.id))
    if link: db.delete(link)
    db.add(ReservationRoom(reservation_id=stay.reservation_id, room_id=target.id))
    stay.room_id = target.id
    source.status = "dirty"; target.status = "occupied"
    audit(db, user.id, "move", "stay", stay.id, {"from_room_id": source.id, "to_room_id": target.id, "move_id": move.id})
    db.commit(); return {"move_id": move.id, "stay_id": stay.id, "from_room_id": source.id, "to_room_id": target.id, "effective_at": move.effective_at}


@router.get("/stays/{stay_id}/moves")
def list_room_moves(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    if not db.get(Stay, stay_id): raise HTTPException(status_code=404, detail="Stay not found")
    rows = db.scalars(select(RoomMove).where(RoomMove.stay_id == stay_id).order_by(RoomMove.effective_at, RoomMove.id)).all()
    return [{"id": m.id, "stay_id": m.stay_id, "from_room_id": m.from_room_id, "to_room_id": m.to_room_id, "effective_at": m.effective_at, "reason": m.reason, "created_by": m.created_by} for m in rows]


@router.post("/reservations/{reservation_id}/split", status_code=201)
def split_reservation(reservation_id: int, payload: ReservationSplitCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    source = db.get(Reservation, reservation_id)
    if not source: raise HTTPException(status_code=404, detail="Reservation not found")
    if source.status != "reserved": raise HTTPException(status_code=409, detail="Reservation split is only allowed before check-in")
    current_rooms = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation_id)).all()
    if not set(payload.room_ids).issubset(set(current_rooms)):
        raise HTTPException(status_code=400, detail="Split contains a room not assigned to the reservation")
    if set(payload.room_ids) == set(current_rooms): raise HTTPException(status_code=400, detail="At least one room must remain on the source reservation")
    check_in = payload.split_check_in or source.check_in; check_out = payload.split_check_out or source.check_out
    if check_out <= check_in or check_in != source.check_in or check_out != source.check_out:
        raise HTTPException(status_code=400, detail="Initial split keeps the source reservation dates")
    guest_id = payload.new_guest_id or source.guest_id
    if not db.get(Guest, guest_id): raise HTTPException(status_code=400, detail="New guest does not exist")
    source_folio = db.scalar(select(Folio).where(Folio.reservation_id == source.id))
    if source_folio and db.scalar(select(FolioItem.id).where(FolioItem.folio_id == source_folio.id, FolioItem.stay_id.in_(select(Stay.id).where(Stay.reservation_id == source.id, Stay.room_id.in_(payload.room_ids))))):
        pass
    from .models import Payment
    if source_folio and db.scalar(select(Payment.id).where(Payment.folio_id == source_folio.id)):
        raise HTTPException(status_code=409, detail="Cannot split a reservation after payments have been posted to its folio")
    new_reservation = Reservation(guest_id=guest_id, check_in=source.check_in, check_out=source.check_out, status="reserved", notes=source.notes)
    db.add(new_reservation); db.flush()
    new_folio = Folio(reservation_id=new_reservation.id, status="open"); db.add(new_folio); db.flush()
    selected_stays = db.scalars(select(Stay).where(Stay.reservation_id == source.id, Stay.room_id.in_(payload.room_ids))).all()
    if len(selected_stays) != len(payload.room_ids): raise HTTPException(status_code=409, detail="Selected rooms do not have corresponding stays")
    for room_id in payload.room_ids:
        link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == source.id, ReservationRoom.room_id == room_id))
        if link: db.delete(link)
        db.add(ReservationRoom(reservation_id=new_reservation.id, room_id=room_id))
    for stay in selected_stays:
        stay.reservation_id = new_reservation.id
        stay.guest_id = guest_id
        for item in db.scalars(select(FolioItem).where(FolioItem.stay_id == stay.id, FolioItem.folio_id == source_folio.id)).all() if source_folio else []:
            item.folio_id = new_folio.id
    split = ReservationSplit(source_reservation_id=source.id, new_reservation_id=new_reservation.id, reason=payload.reason, split_check_in=check_in, split_check_out=check_out, created_by=user.id)
    db.add(split); audit(db, user.id, "split", "reservation", source.id, {"new_reservation_id": new_reservation.id, "room_ids": payload.room_ids})
    db.commit()
    return {"split_id": split.id, "source_reservation_id": source.id, "new_reservation_id": new_reservation.id, "room_ids": payload.room_ids, "new_folio_id": new_folio.id}
