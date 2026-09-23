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

