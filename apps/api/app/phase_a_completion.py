from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from secrets import token_hex

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import require_roles
from .db import Base, get_db
from .ledger import post_deposit_received, post_transaction
from .models import AuditLog, DepositTransaction, Folio, FolioItem, Guest, Payment, Reservation, ReservationRoom, Room, User
from .pms_core import Stay
from .stay_lifecycle import StayFolioWindow

router = APIRouter(prefix="", tags=["phase-a-completion"])
MONEY = Decimal("0.01")


class OccupantGuestChange(BaseModel):
    guest_id: int
    reason: str | None = Field(default=None, max_length=300)


class DepositTransferCreate(BaseModel):
    target_stay_id: int
    amount: Decimal = Field(gt=0)
    reason: str | None = Field(default=None, max_length=300)


class DepositRefundCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    payment_method: str = Field(default="cash", max_length=30)
    reference: str | None = Field(default=None, max_length=100)
    reason: str | None = Field(default=None, max_length=300)


class DepositApplyCreate(BaseModel):
    folio_id: int
    amount: Decimal = Field(gt=0)
    reason: str | None = Field(default=None, max_length=300)


class FolioWindowUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    payer_type: str | None = Field(default=None, max_length=30)
    guest_id: int | None = None
    group_id: int | None = None
    status: str | None = Field(default=None, pattern="^(open|closed)$")


class FolioItemRouteCreate(BaseModel):
    window_id: int
    notes: str | None = Field(default=None, max_length=300)


class StayCheckInPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class FolioItemRouting(Base):
    __tablename__ = "folio_item_routing"
    __table_args__ = (UniqueConstraint("folio_item_id", name="uq_folio_item_routing_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    folio_item_id: Mapped[int] = mapped_column(ForeignKey("folio_items.id", ondelete="CASCADE"), index=True)
    window_id: Mapped[int] = mapped_column(ForeignKey("stay_folio_windows.id", ondelete="CASCADE"), index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def stay_or_404(db: Session, stay_id: int) -> Stay:
    stay = db.get(Stay, stay_id)
    if not stay:
        raise HTTPException(status_code=404, detail="Stay not found")
    return stay


def deposit_balance(db: Session, stay_id: int) -> Decimal:
    transactions = db.scalars(
        select(DepositTransaction).where(DepositTransaction.stay_id == stay_id).order_by(DepositTransaction.created_at, DepositTransaction.id)
    ).all()
    positive = {"received", "adjusted", "transfer_in"}
    return money(sum((item.amount if item.transaction_type in positive else -item.amount for item in transactions), Decimal("0.00")))


def deposit_reference() -> str:
    return f"DEP-{token_hex(5).upper()}"


def validate_window(db: Session, stay: Stay, window_id: int) -> StayFolioWindow:
    window = db.get(StayFolioWindow, window_id)
    if not window or window.stay_id != stay.id:
        raise HTTPException(status_code=404, detail="Folio window not found for this stay")
    if window.status != "open":
        raise HTTPException(status_code=409, detail="Folio window is closed")
    return window


@router.post("/stays/{stay_id}/check-in", status_code=201)
def check_in_stay(stay_id: int, payload: StayCheckInPayload | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    if stay.status != "reserved":
        raise HTTPException(status_code=409, detail="Only a reserved room stay can be checked in")
    room = db.get(Room, stay.room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.status not in {"reserved", "available"}:
        raise HTTPException(status_code=409, detail="Room is not ready for check-in")
    stay.status = "checked_in"
    stay.actual_check_in = datetime.utcnow()
    room.status = "occupied"
    reservation = db.get(Reservation, stay.reservation_id)
    if reservation and reservation.status == "reserved":
        reservation.status = "checked_in"
        reservation.checked_in_at = stay.actual_check_in
    audit(db, user.id, "stay_check_in", "stay", stay.id, {"room_id": room.id, "reason": payload.reason if payload else None})
    db.commit()
    return {"stay_id": stay.id, "reservation_id": stay.reservation_id, "room_id": room.id, "status": stay.status, "actual_check_in": stay.actual_check_in}


@router.patch("/stays/{stay_id}/occupants/{occupant_id}/guest")
def change_stay_occupant_guest(stay_id: int, occupant_id: int, payload: OccupantGuestChange, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    from .models import StayOccupant

    occupant = db.scalar(select(StayOccupant).where(StayOccupant.id == occupant_id, StayOccupant.stay_id == stay.id))
    if not occupant:
        raise HTTPException(status_code=404, detail="Stay occupant not found")
    guest = db.get(Guest, payload.guest_id)
    if not guest:
        raise HTTPException(status_code=400, detail="Guest does not exist")
    if occupant.guest_id == payload.guest_id:
        raise HTTPException(status_code=400, detail="Occupant already uses this guest")
    duplicate = db.scalar(
        select(StayOccupant.id).where(StayOccupant.stay_id == stay.id, StayOccupant.guest_id == payload.guest_id, StayOccupant.id != occupant.id)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="Guest is already an occupant of this room stay")
    old_guest_id = occupant.guest_id
    occupant.guest_id = payload.guest_id
    audit(db, user.id, "occupant_guest_change", "stay_occupant", occupant.id, {"stay_id": stay.id, "from_guest_id": old_guest_id, "to_guest_id": guest.id, "reason": payload.reason})
    db.commit()
    return {"id": occupant.id, "stay_id": stay.id, "guest_id": occupant.guest_id, "role": occupant.role, "is_primary": occupant.is_primary, "check_in": occupant.check_in, "check_out": occupant.check_out}


@router.post("/stays/{stay_id}/occupants/share", status_code=201)
def share_stay_with_occupant(stay_id: int, payload: OccupantGuestChange, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    from .models import StayOccupant

    guest = db.get(Guest, payload.guest_id)
    if not guest:
        raise HTTPException(status_code=400, detail="Guest does not exist")
    existing = db.scalar(select(StayOccupant.id).where(StayOccupant.stay_id == stay.id, StayOccupant.guest_id == payload.guest_id))
    if existing:
        raise HTTPException(status_code=409, detail="Guest already shares this room stay")
    occupant = StayOccupant(stay_id=stay.id, guest_id=guest.id, role="occupant", is_primary=False, check_in=stay.check_in, check_out=stay.check_out, notes=payload.reason)
    db.add(occupant)
    audit(db, user.id, "room_share_add", "stay_occupant", stay.id, {"stay_id": stay.id, "guest_id": guest.id, "reason": payload.reason})
    db.commit()
    return {"id": occupant.id, "stay_id": stay.id, "guest_id": occupant.guest_id, "role": occupant.role, "is_primary": occupant.is_primary, "check_in": occupant.check_in, "check_out": occupant.check_out}


@router.post("/stays/{stay_id}/deposits/transfer", status_code=201)
def transfer_deposit(stay_id: int, payload: DepositTransferCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    source = stay_or_404(db, stay_id)
    target = stay_or_404(db, payload.target_stay_id)
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Source and target stay must be different")
    available = deposit_balance(db, source.id)
    amount = money(payload.amount)
    if amount > available:
        raise HTTPException(status_code=409, detail=f"Transfer exceeds deposit balance of {available}")
    reference = deposit_reference()
    out_tx = DepositTransaction(stay_id=source.id, transaction_type="transfer_out", amount=amount, reference=reference, notes=payload.reason, created_by=user.id)
    in_tx = DepositTransaction(stay_id=target.id, transaction_type="transfer_in", amount=amount, reference=reference, notes=f"Transfer from stay #{source.id}: {payload.reason or 'Deposit transfer'}", created_by=user.id)
    db.add_all([out_tx, in_tx])
    audit(db, user.id, "deposit_transfer", "stay", source.id, {"target_stay_id": target.id, "amount": str(amount), "reference": reference})
    db.commit()
    return {"reference": reference, "from_stay_id": source.id, "to_stay_id": target.id, "amount": amount, "source_balance": deposit_balance(db, source.id), "target_balance": deposit_balance(db, target.id)}


@router.post("/stays/{stay_id}/deposits/refund", status_code=201)
def refund_deposit(stay_id: int, payload: DepositRefundCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    amount = money(payload.amount)
    available = deposit_balance(db, stay.id)
    if amount > available:
        raise HTTPException(status_code=409, detail=f"Refund exceeds deposit balance of {available}")
    tx = DepositTransaction(stay_id=stay.id, transaction_type="refunded", amount=amount, payment_method=payload.payment_method, reference=payload.reference or deposit_reference(), notes=payload.reason, created_by=user.id)
    db.add(tx)
    db.flush()
    reservation = db.get(Reservation, stay.reservation_id)
    if reservation:
        post_transaction(db, transaction_type="deposit_refund", description=f"Deposit refund #{tx.id}", reference_type="deposit", reference_id=str(tx.id), folio_id=None, reservation_id=reservation.id, created_by=user.id, lines=[
            {"account": "Guest Deposits", "direction": "debit", "amount": amount, "stay_id": stay.id, "payment_method": payload.payment_method, "reference": tx.reference},
            {"account": {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(payload.payment_method, "Other Payment"), "direction": "credit", "amount": amount, "stay_id": stay.id, "payment_method": payload.payment_method, "reference": tx.reference},
        ])
    audit(db, user.id, "deposit_refund", "deposit_transaction", tx.id, {"stay_id": stay.id, "amount": str(amount), "reference": tx.reference})
    db.commit()
    return {"id": tx.id, "stay_id": stay.id, "transaction_type": tx.transaction_type, "amount": tx.amount, "balance": deposit_balance(db, stay.id), "reference": tx.reference}


@router.post("/stays/{stay_id}/deposits/apply", status_code=201)
def apply_deposit(stay_id: int, payload: DepositApplyCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    folio = db.get(Folio, payload.folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio.reservation_id != stay.reservation_id:
        raise HTTPException(status_code=409, detail="Deposit can only be applied to the stay's reservation folio")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is closed")
    amount = money(payload.amount)
    available = deposit_balance(db, stay.id)
    if amount > available:
        raise HTTPException(status_code=409, detail=f"Application exceeds deposit balance of {available}")
    existing_paid = db.scalar(select(FolioItem.id).where(FolioItem.folio_id == folio.id, FolioItem.description.like("Deposit application #%"), FolioItem.stay_id == stay.id))
    if existing_paid:
        pass
    tx = DepositTransaction(stay_id=stay.id, folio_id=folio.id, transaction_type="applied", amount=amount, payment_method="deposit", reference=deposit_reference(), notes=payload.reason, created_by=user.id)
    db.add(tx)
    db.flush()
    payment = Payment(folio_id=folio.id, amount=amount, method="deposit", reference=tx.reference)
    db.add(payment)
    db.flush()
    reservation = db.get(Reservation, stay.reservation_id)
    if reservation:
        post_transaction(db, transaction_type="deposit_applied", description=f"Deposit application #{tx.id}", reference_type="deposit", reference_id=str(tx.id), folio_id=folio.id, reservation_id=reservation.id, created_by=user.id, lines=[
            {"account": "Guest Deposits", "direction": "debit", "amount": amount, "folio_id": folio.id, "stay_id": stay.id, "payment_method": "deposit", "reference": tx.reference},
            {"account": "Guest Receivables", "direction": "credit", "amount": amount, "folio_id": folio.id, "stay_id": stay.id, "payment_method": "deposit", "reference": tx.reference},
        ])
    audit(db, user.id, "deposit_apply", "deposit_transaction", tx.id, {"stay_id": stay.id, "folio_id": folio.id, "amount": str(amount), "reference": tx.reference})
    db.commit()
    return {"deposit_transaction_id": tx.id, "payment_id": payment.id, "stay_id": stay.id, "folio_id": folio.id, "amount": amount, "remaining_deposit": deposit_balance(db, stay.id), "reference": tx.reference}


@router.patch("/stays/{stay_id}/folio-windows/{window_id}")
def update_folio_window(stay_id: int, window_id: int, payload: FolioWindowUpdatePayload, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = stay_or_404(db, stay_id)
    window = db.get(StayFolioWindow, window_id)
    if not window or window.stay_id != stay.id:
        raise HTTPException(status_code=404, detail="Folio window not found for this stay")
    if payload.payer_type is not None and payload.payer_type not in {"guest", "group", "company", "other"}:
        raise HTTPException(status_code=400, detail="Invalid payer type")
    if payload.guest_id is not None and not db.get(Guest, payload.guest_id):
        raise HTTPException(status_code=400, detail="Window payer guest does not exist")
    if payload.status == "closed" and window.status == "closed":
        raise HTTPException(status_code=409, detail="Folio window is already closed")
    if payload.name is not None:
        window.name = payload.name.strip()
    if payload.payer_type is not None:
        window.payer_type = payload.payer_type
    if payload.guest_id is not None:
        window.guest_id = payload.guest_id
    if payload.group_id is not None:
        window.group_id = payload.group_id
    if payload.status is not None:
        window.status = payload.status
    audit(db, user.id, "folio_window_update", "stay_folio_window", window.id, {"stay_id": stay.id, "name": window.name, "status": window.status})
    db.commit()
    return {"id": window.id, "folio_id": window.folio_id, "stay_id": window.stay_id, "name": window.name, "payer_type": window.payer_type, "guest_id": window.guest_id, "group_id": window.group_id, "status": window.status}


@router.post("/folio-items/{item_id}/route", status_code=201)
def route_folio_item(item_id: int, payload: FolioItemRouteCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    item = db.get(FolioItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Folio item not found")
    window = db.get(StayFolioWindow, payload.window_id)
    if not window:
        raise HTTPException(status_code=404, detail="Folio window not found")
    if window.status != "open":
        raise HTTPException(status_code=409, detail="Folio window is closed")
    if item.folio_id != window.folio_id:
        raise HTTPException(status_code=409, detail="Folio item and window belong to different folios")
    if item.stay_id is not None and item.stay_id != window.stay_id:
        raise HTTPException(status_code=409, detail="Stay charge must be routed to a window for the same room stay")
    route = db.scalar(select(FolioItemRouting).where(FolioItemRouting.folio_item_id == item.id))
    if route is None:
        route = FolioItemRouting(folio_item_id=item.id, window_id=window.id, notes=payload.notes, created_by=user.id)
        db.add(route)
    else:
        route.window_id = window.id
        route.notes = payload.notes
        route.created_by = user.id
        route.updated_at = datetime.utcnow()
    audit(db, user.id, "folio_item_route", "folio_item", item.id, {"window_id": window.id, "stay_id": window.stay_id})
    db.commit()
    return {"id": route.id, "folio_item_id": route.folio_item_id, "window_id": route.window_id, "notes": route.notes}


@router.get("/stays/{stay_id}/folio-routing")
def list_folio_routing(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    stay = stay_or_404(db, stay_id)
    rows = db.execute(
        select(FolioItemRouting, FolioItem, StayFolioWindow)
        .join(FolioItem, FolioItem.id == FolioItemRouting.folio_item_id)
        .join(StayFolioWindow, StayFolioWindow.id == FolioItemRouting.window_id)
        .where(StayFolioWindow.stay_id == stay.id)
        .order_by(FolioItemRouting.id)
    ).all()
    return [{"routing_id": route.id, "folio_item_id": item.id, "description": item.description, "category": item.category, "amount": money(Decimal(item.quantity) * Decimal(item.unit_price) - Decimal(item.discount)), "window_id": window.id, "window_name": window.name, "notes": route.notes} for route, item, window in rows]
