from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .business_date import get_current_business_date
from .financial_authority import folio_ledger_summary
from .ledger import post_deposit_received
from .models import AuditLog, DepositTransaction, Folio, FolioItem, Guest, Reservation, ReservationRoom, Room, RoomType, StayRateSegment, User
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


def room_stay_ids(db: Session, reservation_id: int) -> list[int]:
    return db.scalars(select(Stay.id).where(Stay.reservation_id == reservation_id).order_by(Stay.id)).all()



def atomic_checkout(
    reservation_id: int,
    db: Session,
    user: User,
) -> dict:
    """Authoritative checkout state transition shared by the API checkout route."""
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in":
        raise HTTPException(status_code=409, detail="Reservation is not checked in")

    folio = db.scalar(
        select(Folio)
        .where(Folio.reservation_id == reservation.id)
        .order_by(Folio.id)
        .limit(1)
    )
    if not folio:
        raise HTTPException(status_code=409, detail="Reservation has no folio")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is already closed")

    # Final checkout must first reconcile any room night that belongs to the
    # current hotel business date. The reconciler is idempotent and uses the
    # existing authoritative financial ledger.
    business_date = get_current_business_date(db)
    post_accrued_room_charges(db, reservation, folio, business_date, user.id)

    balance = folio_balance(db, folio)
    if balance != Decimal("0.00"):
        raise HTTPException(
            status_code=409,
            detail=f"Guest must settle the folio before checkout; outstanding balance is {balance}",
        )

    room_ids = db.scalars(
        select(ReservationRoom.room_id)
        .where(ReservationRoom.reservation_id == reservation.id)
    ).all()
    checkout_at = datetime.utcnow()

    folio.status = "closed"
    reservation.status = "checked_out"

    for stay in db.scalars(
        select(Stay).where(Stay.reservation_id == reservation.id).order_by(Stay.id)
    ).all():
        stay.status = "completed"
        stay.actual_check_out = stay.actual_check_out or checkout_at

    for room_id in room_ids:
        room = db.get(Room, room_id)
        if room and room.status == "occupied":
            room.status = "dirty"

    audit(
        db,
        user.id,
        "atomic_checkout",
        "reservation",
        reservation.id,
        {
            "folio_id": folio.id,
            "room_ids": room_ids,
            "business_date": str(business_date),
            "checked_out_at": checkout_at.isoformat(),
        },
    )
    db.commit()

    return {
        "reservation_id": reservation.id,
        "folio_id": folio.id,
        "status": reservation.status,
        "folio_status": folio.status,
        "room_ids": room_ids,
        "room_status": "dirty",
    }


def post_accrued_room_charges(
    db: Session,
    reservation: Reservation,
    folio: Folio,
    business_date: date,
    user_id: int,
) -> int:
    """Delegate checkout room-night posting to the exact stay/date reconciler."""
    from .room_charge_integrity import post_accrued_room_charges as reconcile_room_charges
    return reconcile_room_charges(db, reservation, folio, business_date, user_id)

