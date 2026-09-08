from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import AuditLog, Expense, Folio, FolioItem, Payment, Reservation, Room, User

router = APIRouter(prefix="/api", tags=["night-audit"])
MONEY = Decimal("0.01")


def money(value: Decimal | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def audit(db: Session, user_id: int, action: str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, entity_type="night_audit", entity_id=str(date.today()), details=json.dumps(details)))


class ClosingConfirm(BaseModel):
    notes: str | None = None


@router.get("/night-audit/preview")
def preview(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    business_date = date.today()
    folios = db.scalars(select(Folio)).all()
    room_revenue = Decimal("0.00")
    other_revenue = Decimal("0.00")
    food_revenue = Decimal("0.00")
    service_charge = Decimal("0.00")
    for folio in folios:
        items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id)).all()
        for item in items:
            net = max(Decimal("0.00"), Decimal(item.quantity) * Decimal(item.unit_price) - Decimal(item.discount))
            category = item.category.strip().lower()
            if category == "room": room_revenue += net
            elif category in {"food", "restaurant", "room_service", "beverage", "drink", "snack"}: food_revenue += net
            else: other_revenue += net
    service_charge = money(food_revenue * Decimal("0.10"))
    payments = db.scalars(select(Payment)).all()
    payment_totals: dict[str, Decimal] = {}
    for payment in payments:
        payment_totals[payment.method] = payment_totals.get(payment.method, Decimal("0.00")) + Decimal(payment.amount)
    rooms = db.scalars(select(Room)).all()
    status_counts = {status: 0 for status in ("available", "reserved", "occupied", "dirty", "out_of_order")}
    for room in rooms:
        status_counts[room.status] = status_counts.get(room.status, 0) + 1
    arrivals = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_in == business_date, Reservation.status.in_(("reserved", "checked_in")))) or 0
    departures = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_out == business_date, Reservation.status == "checked_in")) or 0
    in_house = db.scalar(select(func.count(Reservation.id)).where(Reservation.status == "checked_in")) or 0
    no_shows = db.scalar(select(func.count(Reservation.id)).where(Reservation.check_in <= business_date, Reservation.status == "no_show")) or 0
    expenses = db.scalar(select(func.coalesce(func.sum(Expense.amount), 0))) or Decimal("0.00")
    gross_revenue = money(room_revenue + food_revenue + service_charge + other_revenue)
    paid_total = money(sum(payment_totals.values(), Decimal("0.00")))
    outstanding = Decimal("0.00")
    for folio in folios:
        items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id)).all()
        total = sum((max(Decimal("0.00"), Decimal(i.quantity) * Decimal(i.unit_price) - Decimal(i.discount)) for i in items), Decimal("0.00"))
        food_net = sum((max(Decimal("0.00"), Decimal(i.quantity) * Decimal(i.unit_price) - Decimal(i.discount)) for i in items if i.category.strip().lower() in {"food", "restaurant", "room_service", "beverage", "drink", "snack"}), Decimal("0.00"))
        total += food_net * Decimal("0.10")
        paid = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.folio_id == folio.id)) or Decimal("0.00")
        outstanding += max(Decimal("0.00"), total - Decimal(paid))
    return {
        "business_date": business_date,
        "generated_at": datetime.utcnow(),
        "occupancy": {"total_rooms": len(rooms), "occupied_rooms": status_counts.get("occupied", 0), "reserved_rooms": status_counts.get("reserved", 0), "available_rooms": status_counts.get("available", 0), "dirty_rooms": status_counts.get("dirty", 0), "out_of_order_rooms": status_counts.get("out_of_order", 0), "in_house_reservations": in_house},
        "movement": {"arrivals": arrivals, "departures": departures, "no_shows": no_shows},
        "revenue": {"room": money(room_revenue), "food": money(food_revenue), "food_service_charge": service_charge, "other": money(other_revenue), "gross": gross_revenue},
        "payments": {method: money(amount) for method, amount in payment_totals.items()} | {"total": paid_total},
        "outstanding": money(outstanding),
        "expenses": money(expenses),
        "net_operating": money(gross_revenue - Decimal(expenses)),
    }


@router.post("/night-audit/close")
def close_day(payload: ClosingConfirm | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    existing = db.scalar(select(AuditLog.id).where(AuditLog.entity_type == "night_audit", AuditLog.entity_id == str(date.today()), AuditLog.action == "daily_close").limit(1))
    if existing:
        raise HTTPException(status_code=409, detail="Daily closing is already completed for today")
    summary = preview(db=db)
    audit(db, user.id, "daily_close", {"business_date": str(date.today()), "summary": summary, "notes": payload.notes if payload else None})
    db.commit()
    return {"status": "closed", "business_date": date.today(), "summary": summary}
