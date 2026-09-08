from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .housekeeping import router as housekeeping_router
from .models import AuditLog, Folio, FolioItem, Guest, Payment, Reservation, ReservationRoom, Room, RoomType, User
from .schemas import BillingSummaryResponse, FolioItemCreate, FolioItemResponse, FolioResponse, PaymentCreate, PaymentResponse

router = APIRouter(prefix="/api", tags=["billing"])
MONEY = Decimal("0.01")
router.include_router(housekeeping_router)


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def item_line_total(item: FolioItem) -> Decimal:
    gross = Decimal(item.quantity) * Decimal(item.unit_price)
    return money(max(Decimal("0.00"), gross - Decimal(item.discount)))


def build_folio_response(db: Session, folio: Folio) -> FolioResponse:
    items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id).order_by(FolioItem.id)).all()
    payments = db.scalars(select(Payment).where(Payment.folio_id == folio.id).order_by(Payment.id)).all()
    subtotal = money(sum((Decimal(i.quantity) * Decimal(i.unit_price) for i in items), Decimal("0.00")))
    discounts = money(sum((Decimal(i.discount) for i in items), Decimal("0.00")))
    total = money(sum((item_line_total(i) for i in items), Decimal("0.00")))
    paid = money(sum((Decimal(p.amount) for p in payments), Decimal("0.00")))
    balance = money(max(Decimal("0.00"), total - paid))
    return FolioResponse(
        id=folio.id, reservation_id=folio.reservation_id, status=folio.status,
        items=[FolioItemResponse(id=i.id, description=i.description, category=i.category, quantity=i.quantity, unit_price=i.unit_price, discount=i.discount, line_total=item_line_total(i)) for i in items],
        payments=[PaymentResponse.model_validate(p) for p in payments],
        subtotal=subtotal, discounts=discounts, total=total, paid=paid, balance=balance,
    )


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int, details: dict):
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


@router.get("/billing", response_model=list[BillingSummaryResponse])
def list_billing(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    rows = db.execute(select(Folio, Reservation, Guest.full_name).join(Reservation, Reservation.id == Folio.reservation_id).join(Guest, Guest.id == Reservation.guest_id).order_by(Folio.id.desc())).all()
    result = []
    for folio, reservation, guest_name in rows:
        summary = build_folio_response(db, folio)
        result.append(BillingSummaryResponse(folio_id=folio.id, reservation_id=reservation.id, guest_name=guest_name, status=folio.status, total=summary.total, paid=summary.paid, balance=summary.balance))
    return result


@router.get("/folios/{folio_id}", response_model=FolioResponse)
def get_folio(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    return build_folio_response(db, folio)


@router.get("/reservations/{reservation_id}/folio", response_model=FolioResponse)
def reservation_folio(reservation_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    if not db.get(Reservation, reservation_id): raise HTTPException(status_code=404, detail="Reservation not found")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == reservation_id))
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    return build_folio_response(db, folio)


@router.get("/folios/{folio_id}/receipt")
def get_receipt(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    reservation = db.get(Reservation, folio.reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    guest = db.get(Guest, reservation.guest_id)
    if not guest: raise HTTPException(status_code=404, detail="Guest not found")
    summary = build_folio_response(db, folio)
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation.id)).all()
    rooms = [db.get(Room, room_id) for room_id in room_ids]
    return {
        "folio_id": folio.id,
        "reservation_id": reservation.id,
        "status": folio.status,
        "guest": {"full_name": guest.full_name, "phone": guest.phone, "email": guest.email, "address": guest.address},
        "stay": {"check_in": reservation.check_in, "check_out": reservation.check_out, "nights": (reservation.check_out - reservation.check_in).days},
        "rooms": [{"id": room.id, "number": room.number, "room_type_id": room.room_type_id} for room in rooms if room],
        "items": [
            {
                "id": item.id,
                "description": item.description,
                "category": item.category,
                "quantity": float(item.quantity),
                "unit_price": float(item.unit_price),
                "discount": float(item.discount),
                "line_total": float(item.line_total),
            }
            for item in summary.items
        ],
        "payments": [
            {"id": payment.id, "amount": float(payment.amount), "method": payment.method, "reference": payment.reference}
            for payment in summary.payments
        ],
        "subtotal": float(summary.subtotal),
        "discounts": float(summary.discounts),
        "total": float(summary.total),
        "paid": float(summary.paid),
        "balance": float(summary.balance),
    }


@router.post("/folios/{folio_id}/room-charges", response_model=FolioResponse)
def add_room_charges(folio_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    existing = db.scalar(select(FolioItem.id).where(FolioItem.folio_id == folio_id, FolioItem.category == "room").limit(1))
    if existing: return build_folio_response(db, folio)
    reservation = db.get(Reservation, folio.reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    nights = (reservation.check_out - reservation.check_in).days
    if nights <= 0: raise HTTPException(status_code=400, detail="Reservation must have at least one night")
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation.id)).all()
    if not room_ids: raise HTTPException(status_code=409, detail="Reservation has no assigned rooms")
    rooms = [db.get(Room, rid) for rid in room_ids]
    for room in rooms:
        if not room: raise HTTPException(status_code=409, detail="Reservation has an invalid room assignment")
        room_type = db.get(RoomType, room.room_type_id)
        if not room_type: raise HTTPException(status_code=409, detail=f"Room {room.number} has no room type")
        db.add(FolioItem(folio_id=folio.id, description=f"Room {room.number} · {nights} night(s)", category="room", quantity=Decimal(nights), unit_price=money(room_type.base_rate), discount=Decimal("0.00")))
    audit(db, user.id, "add_room_charges", "folio", folio.id, {"reservation_id": reservation.id, "nights": nights, "room_ids": room_ids})
    db.commit(); db.refresh(folio)
    return build_folio_response(db, folio)


@router.post("/folios/{folio_id}/items", response_model=FolioItemResponse, status_code=201)
def add_folio_item(folio_id: int, payload: FolioItemCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    gross = money(payload.quantity * payload.unit_price)
    if payload.discount > gross: raise HTTPException(status_code=400, detail="Discount cannot exceed the line amount")
    item = FolioItem(folio_id=folio_id, **payload.model_dump()); db.add(item); db.flush()
    audit(db, user.id, "add", "folio_item", item.id, {"folio_id": folio_id, "description": item.description, "amount": str(item_line_total(item))})
    db.commit(); db.refresh(item)
    return FolioItemResponse(id=item.id, description=item.description, category=item.category, quantity=item.quantity, unit_price=item.unit_price, discount=item.discount, line_total=item_line_total(item))


@router.delete("/folios/{folio_id}/items/{item_id}", status_code=204)
def remove_folio_item(folio_id: int, item_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id); item = db.get(FolioItem, item_id)
    if not folio or not item or item.folio_id != folio_id: raise HTTPException(status_code=404, detail="Folio item not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    db.delete(item); audit(db, user.id, "remove", "folio_item", item_id, {"folio_id": folio_id}); db.commit()


@router.post("/folios/{folio_id}/payments", response_model=PaymentResponse, status_code=201)
def add_payment(folio_id: int, payload: PaymentCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    summary = build_folio_response(db, folio)
    if payload.amount > summary.balance: raise HTTPException(status_code=400, detail=f"Payment exceeds outstanding balance of {summary.balance}")
    payment = Payment(folio_id=folio_id, amount=money(payload.amount), method=payload.method, reference=payload.reference); db.add(payment); db.flush()
    audit(db, user.id, "payment", "folio", folio_id, {"amount": str(payment.amount), "method": payment.method, "reference": payment.reference})
    db.commit(); db.refresh(payment); return payment


@router.post("/folios/{folio_id}/close", response_model=FolioResponse)
def close_folio(folio_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    summary = build_folio_response(db, folio)
    if summary.balance != Decimal("0.00"): raise HTTPException(status_code=409, detail=f"Cannot close folio with outstanding balance of {summary.balance}")
    folio.status = "closed"; audit(db, user.id, "close", "folio", folio.id, {"total": str(summary.total), "paid": str(summary.paid)}); db.commit(); db.refresh(folio)
    return build_folio_response(db, folio)
