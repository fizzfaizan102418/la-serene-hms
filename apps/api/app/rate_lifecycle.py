from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import AuditLog, BusinessDateState, DepositTransaction, Folio, FolioItem, Guest, Reservation, ReservationRoom, Room, RoomMove, StayOccupant, StayRateSegment, User
from .pms_core import Stay

router = APIRouter(prefix="/api", tags=["rate-lifecycle"])
MONEY = Decimal("0.01")
ACTIVE_STAY_STATUSES = ("reserved", "checked_in")


def money(value: Decimal | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def business_date(db: Session) -> date:
    state = db.get(BusinessDateState, 1)
    if state is None:
        state = BusinessDateState(id=1, current_business_date=date.today(), opened_at=datetime.utcnow())
        db.add(state)
        db.flush()
    return state.current_business_date


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def ensure_rate_segments(db: Session, stay: Stay) -> list[StayRateSegment]:
    rows = db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == stay.id).order_by(StayRateSegment.from_date, StayRateSegment.id)).all()
    if rows:
        return rows
    row = StayRateSegment(
        stay_id=stay.id,
        from_date=stay.check_in,
        to_date=stay.check_out,
        rate=money(stay.agreed_rate + stay.discount_amount),
        discount_percent=money(stay.discount_percent),
        discount_amount=money(stay.discount_amount),
        source="migration",
        notes="Created from legacy stay rate",
    )
    db.add(row)
    db.flush()
    return [row]


def active_segment(db: Session, stay: Stay, on_date: date) -> StayRateSegment:
    rows = db.scalars(
        select(StayRateSegment)
        .where(
            StayRateSegment.stay_id == stay.id,
            StayRateSegment.from_date <= on_date,
            StayRateSegment.to_date > on_date,
        )
        .order_by(StayRateSegment.from_date.desc(), StayRateSegment.id.desc())
    ).all()
    if rows:
        return rows[0]
    rows = ensure_rate_segments(db, stay)
    candidates = [r for r in rows if r.from_date < stay.check_out]
    if not candidates:
        raise HTTPException(status_code=409, detail="Stay has no usable rate segment")
    return candidates[-1]


def split_segment_at(db: Session, segment: StayRateSegment, split_date: date) -> tuple[StayRateSegment | None, StayRateSegment | None]:
    if split_date <= segment.from_date or split_date >= segment.to_date:
        return None, None
    values = {
        "rate": segment.rate,
        "discount_percent": segment.discount_percent,
        "discount_amount": segment.discount_amount,
        "rate_plan": segment.rate_plan,
        "source": segment.source,
        "notes": segment.notes,
    }
    left = StayRateSegment(stay_id=segment.stay_id, from_date=segment.from_date, to_date=split_date, **values)
    right = StayRateSegment(stay_id=segment.stay_id, from_date=split_date, to_date=segment.to_date, **values)
    db.delete(segment)
    db.flush()
    db.add_all([left, right])
    db.flush()
    return left, right


def append_rate_segment(
    db: Session,
    stay: Stay,
    from_date: date,
    to_date: date,
    gross_rate: Decimal,
    discount_percent: Decimal = Decimal("0"),
    discount_amount: Decimal = Decimal("0"),
    source: str = "lifecycle",
    rate_plan: str | None = None,
    notes: str | None = None,
) -> StayRateSegment:
    if to_date <= from_date:
        raise HTTPException(status_code=400, detail="Rate segment end must be after start")
    if from_date < stay.check_in or to_date > stay.check_out:
        raise HTTPException(status_code=400, detail="Rate segment dates must be within the stay")
    if discount_percent and discount_amount:
        raise HTTPException(status_code=400, detail="Use either percentage or fixed discount")
    gross = money(gross_rate)
    discount = money(gross * discount_percent / Decimal("100")) if discount_percent else money(discount_amount)
    discount = min(discount, gross)
    segment = StayRateSegment(
        stay_id=stay.id,
        from_date=from_date,
        to_date=to_date,
        rate=gross,
        discount_percent=money(discount_percent),
        discount_amount=discount,
        rate_plan=rate_plan,
        source=source,
        notes=notes,
    )
    db.add(segment)
    db.flush()
    return segment


class StayOverviewRequest(BaseModel):
    pass


class RateOverride(BaseModel):
    stay_id: int
    rate: Decimal = Field(ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    rate_plan: str | None = Field(default=None, max_length=80)
    notes: str | None = None


class RateAwareExtension(BaseModel):
    new_check_out: date
    rate_overrides: list[RateOverride] = Field(default_factory=list)


class RateAwareRoomMove(BaseModel):
    to_room_id: int
    reason: str | None = Field(default=None, max_length=300)
    rate: Decimal | None = Field(default=None, ge=0)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    rate_plan: str | None = Field(default=None, max_length=80)
    notes: str | None = None


def guest_summary(db: Session, guest_id: int) -> dict:
    guest = db.get(Guest, guest_id)
    return {"id": guest_id, "name": guest.full_name if guest else f"Guest #{guest_id}"}


def room_conflict(db: Session, room_id: int, check_in: date, check_out: date, exclude_stay_id: int | None = None) -> bool:
    stmt = (
        select(Stay.id)
        .where(
            Stay.room_id == room_id,
            Stay.status.in_(ACTIVE_STAY_STATUSES),
            Stay.check_in < check_out,
            Stay.check_out > check_in,
        )
        .limit(1)
    )
    if exclude_stay_id is not None:
        stmt = stmt.where(Stay.id != exclude_stay_id)
    return db.scalar(stmt) is not None


@router.get("/reservations/{reservation_id}/stay-overview")
def reservation_stay_overview(reservation_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == reservation.id).order_by(Folio.id).limit(1))
    stays = db.scalars(select(Stay).where(Stay.reservation_id == reservation.id).order_by(Stay.id)).all()
    result = []
    for stay in stays:
        room = db.get(Room, stay.room_id)
        occupants = db.execute(
            select(StayOccupant, Guest.full_name)
            .join(Guest, Guest.id == StayOccupant.guest_id)
            .where(StayOccupant.stay_id == stay.id)
            .order_by(StayOccupant.is_primary.desc(), StayOccupant.id)
        ).all()
        segments = ensure_rate_segments(db, stay)
        deposits = db.scalars(select(DepositTransaction).where(DepositTransaction.stay_id == stay.id).order_by(DepositTransaction.created_at, DepositTransaction.id)).all()
        deposit_balance = money(sum((item.amount if item.transaction_type in {"received", "adjusted"} else -item.amount for item in deposits), Decimal("0.00")))
        result.append(
            {
                "id": stay.id,
                "room_id": stay.room_id,
                "room_number": room.number if room else None,
                "room_status": room.status if room else None,
                "status": stay.status,
                "check_in": stay.check_in,
                "check_out": stay.check_out,
                "actual_check_in": stay.actual_check_in,
                "actual_check_out": stay.actual_check_out,
                "guest": guest_summary(db, stay.guest_id),
                "agreed_rate": stay.agreed_rate,
                "discount_percent": stay.discount_percent,
                "discount_amount": stay.discount_amount,
                "deposit_required": stay.deposit_required,
                "deposit_received": deposit_balance,
                "occupants": [{"id": o.id, "guest_id": o.guest_id, "guest_name": name, "role": o.role, "is_primary": o.is_primary, "check_in": o.check_in, "check_out": o.check_out, "notes": o.notes} for o, name in occupants],
                "rate_segments": [{"id": s.id, "from_date": s.from_date, "to_date": s.to_date, "rate": s.rate, "discount_percent": s.discount_percent, "discount_amount": s.discount_amount, "net_rate": money(s.rate - s.discount_amount), "rate_plan": s.rate_plan, "source": s.source, "notes": s.notes} for s in segments],
            }
        )
    db.commit()
    return {
        "reservation": {"id": reservation.id, "guest_id": reservation.guest_id, "guest_name": guest_summary(db, reservation.guest_id)["name"], "check_in": reservation.check_in, "check_out": reservation.check_out, "status": reservation.status, "folio_id": folio.id if folio else None},
        "stays": result,
    }


@router.post("/reservations/{reservation_id}/extend-rate-aware")
def extend_reservation_rate_aware(reservation_id: int, payload: RateAwareExtension, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in":
        raise HTTPException(status_code=409, detail="Only an in-house reservation can be extended")
    if payload.new_check_out <= reservation.check_out:
        raise HTTPException(status_code=400, detail="New check-out date must be later than the current check-out")
    if payload.new_check_out <= business_date(db):
        raise HTTPException(status_code=400, detail="New check-out date must be later than the current business date")
    overrides = {item.stay_id: item for item in payload.rate_overrides}
    stays = db.scalars(select(Stay).where(Stay.reservation_id == reservation.id, Stay.status == "checked_in").order_by(Stay.id)).all()
    if not stays:
        raise HTTPException(status_code=409, detail="Reservation has no checked-in stays")
    old_checkout = reservation.check_out
    conflicts = []
    for stay in stays:
        if room_conflict(db, stay.room_id, old_checkout, payload.new_check_out, stay.id):
            room = db.get(Room, stay.room_id)
            conflicts.append(room.number if room else str(stay.room_id))
    if conflicts:
        raise HTTPException(status_code=409, detail=f"Extension conflicts with room(s): {', '.join(conflicts)}")
    if any(item.stay_id not in {stay.id for stay in stays} for item in payload.rate_overrides):
        raise HTTPException(status_code=400, detail="Rate override references a stay outside this reservation")

    for stay in stays:
        ensure_rate_segments(db, stay)
        base = active_segment(db, stay, max(old_checkout - __import__("datetime").timedelta(days=1), stay.check_in))
        override = overrides.get(stay.id)
        stay.check_out = payload.new_check_out
        if override:
            append_rate_segment(
                db,
                stay,
                old_checkout,
                payload.new_check_out,
                override.rate,
                override.discount_percent,
                override.discount_amount,
                source="extension",
                rate_plan=override.rate_plan,
                notes=override.notes,
            )
        else:
            append_rate_segment(
                db,
                stay,
                old_checkout,
                payload.new_check_out,
                base.rate,
                base.discount_percent,
                base.discount_amount,
                source="extension",
                rate_plan=base.rate_plan,
                notes="Carried forward from prior rate segment",
            )
    reservation.check_out = payload.new_check_out
    audit(db, user.id, "extend_rate_aware", "reservation", reservation.id, {"from_check_out": str(old_checkout), "to_check_out": str(payload.new_check_out), "rate_overrides": [item.model_dump(mode="json") for item in payload.rate_overrides]})
    db.commit()
    return {"reservation_id": reservation.id, "old_check_out": old_checkout, "new_check_out": reservation.check_out, "stays": [{"stay_id": stay.id, "check_out": stay.check_out} for stay in stays]}


@router.post("/stays/{stay_id}/move-rate-aware", status_code=201)
def move_stay_rate_aware(stay_id: int, payload: RateAwareRoomMove, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = db.get(Stay, stay_id)
    if not stay:
        raise HTTPException(status_code=404, detail="Stay not found")
    if stay.status != "checked_in":
        raise HTTPException(status_code=409, detail="Room move requires a checked-in stay")
    source = db.get(Room, stay.room_id)
    target = db.get(Room, payload.to_room_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or destination room not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Destination room must be different")
    if target.status != "available":
        raise HTTPException(status_code=409, detail="Destination room is not available")
    move_date = business_date(db)
    if not (stay.check_in <= move_date < stay.check_out):
        raise HTTPException(status_code=409, detail="Current business date is outside the stay")
    if room_conflict(db, target.id, move_date, stay.check_out, stay.id):
        raise HTTPException(status_code=409, detail="Destination room is not available for the remainder of the stay")
    if payload.discount_percent and payload.discount_amount:
        raise HTTPException(status_code=400, detail="Use either percentage or fixed discount")

    current = active_segment(db, stay, move_date)
    _, future = split_segment_at(db, current, move_date)
    if future is None:
        future = current
    if payload.rate is not None:
        # Keep historical pricing before the move; replace only the post-move interval.
        gross = money(payload.rate)
        discount = money(gross * payload.discount_percent / Decimal("100")) if payload.discount_percent else money(payload.discount_amount)
        discount = min(discount, gross)
        future.rate = gross
        future.discount_percent = money(payload.discount_percent)
        future.discount_amount = discount
        future.rate_plan = payload.rate_plan
        future.source = "room_move"
        future.notes = payload.notes or "Rate adjusted with room move"
    else:
        future.source = "room_move"
        future.notes = payload.notes or "Rate carried forward across room move"

    move = RoomMove(stay_id=stay.id, from_room_id=source.id, to_room_id=target.id, reason=payload.reason, created_by=user.id)
    db.add(move)
    db.flush()
    link = db.scalar(select(ReservationRoom).where(ReservationRoom.reservation_id == stay.reservation_id, ReservationRoom.room_id == source.id))
    if link:
        link.room_id = target.id
    else:
        db.add(ReservationRoom(reservation_id=stay.reservation_id, room_id=target.id))
    stay.room_id = target.id
    source.status = "dirty"
    target.status = "occupied"
    audit(db, user.id, "move_rate_aware", "stay", stay.id, {"from_room_id": source.id, "to_room_id": target.id, "effective_date": str(move_date), "rate_changed": payload.rate is not None})
    db.commit()
    return {"move_id": move.id, "stay_id": stay.id, "from_room_id": source.id, "to_room_id": target.id, "effective_date": move_date, "future_rate": future.rate, "future_discount_amount": future.discount_amount, "future_net_rate": money(future.rate - future.discount_amount)}
