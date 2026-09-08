from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .financial_models import PaymentRefund
from .financial_ops import router as financial_ops_router
from .front_desk import router as front_desk_router
from .housekeeping import router as housekeeping_router
from .financial_authority import folio_ledger_summary, has_posted_folio_item_transaction
from .ledger import post_folio_charge, post_folio_payment, router as ledger_router
from .models import AuditLog, Folio, FolioItem, Guest, Payment, Reservation, ReservationRoom, Room, StayRateSegment, User
from .night_audit import router as night_audit_router
from .pms_core import Stay
from .pms_domain import router as pms_domain_router
from .phase_a_completion import router as phase_a_completion_router
from .rate_lifecycle import router as rate_lifecycle_router
from .reports import router as reports_router
from .schemas import BillingSummaryResponse, FolioItemCreate, FolioItemResponse, FolioItemUpdate, FolioResponse, PaymentCreate, PaymentResponse
from .stay_lifecycle import router as stay_lifecycle_router

router = APIRouter(prefix="/api", tags=["billing"])
MONEY = Decimal("0.01")
FOOD_SERVICE_CHARGE_RATE = Decimal("0.10")
FOOD_CATEGORIES = {"food", "restaurant", "room_service", "beverage", "drink", "snack"}
router.include_router(housekeeping_router)
router.include_router(reports_router)
router.include_router(night_audit_router)
router.include_router(financial_ops_router)
router.include_router(pms_domain_router)
router.include_router(ledger_router)
router.include_router(stay_lifecycle_router)
router.include_router(rate_lifecycle_router)
router.include_router(phase_a_completion_router)
router.include_router(front_desk_router)


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def item_line_total(item: FolioItem) -> Decimal:
    gross = Decimal(item.quantity) * Decimal(item.unit_price)
    return money(max(Decimal("0.00"), gross - Decimal(item.discount)))


def is_food_item(item: FolioItem) -> bool:
    return item.category.strip().lower() in FOOD_CATEGORIES


def build_folio_response(db: Session, folio: Folio) -> FolioResponse:
    items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id).order_by(FolioItem.id)).all()
    payments = db.scalars(select(Payment).where(Payment.folio_id == folio.id).order_by(Payment.id)).all()
    refunds = db.scalar(select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(PaymentRefund.folio_id == folio.id)) or Decimal("0.00")
    subtotal = money(sum((Decimal(i.quantity) * Decimal(i.unit_price) for i in items), Decimal("0.00")))
    discounts = money(sum((Decimal(i.discount) for i in items), Decimal("0.00")))
    food_net = money(sum((item_line_total(i) for i in items if is_food_item(i)), Decimal("0.00")))
    food_service_charge = money(food_net * FOOD_SERVICE_CHARGE_RATE)
    ledger = folio_ledger_summary(db, folio.id)
    return FolioResponse(id=folio.id, reservation_id=folio.reservation_id, status=folio.status, items=[FolioItemResponse(id=i.id, description=i.description, category=i.category, quantity=i.quantity, unit_price=i.unit_price, discount=i.discount, line_total=item_line_total(i)) for i in items], payments=[PaymentResponse.model_validate(p) for p in payments], subtotal=subtotal, discounts=discounts, food_service_charge=food_service_charge, total=ledger.total, paid=ledger.paid, balance=ledger.balance)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int, details: dict):
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


@router.get("/billing", response_model=list[BillingSummaryResponse])
def list_billing(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    rows = db.execute(select(Folio, Reservation, Guest.full_name).join(Reservation, Reservation.id == Folio.reservation_id).join(Guest, Guest.id == Reservation.guest_id).order_by(Folio.id.desc())).all()
    result = []
    for folio, reservation, guest_name in rows:
        summary = build_folio_response(db, folio)
        result.append(BillingSummaryResponse(folio_id=folio.id, reservation_id=reservation.id, guest_name=guest_name, status=folio.status, total=summary.total, paid=summary.paid, balance=summary.balance))
    return result


@router.get("/folios/{folio_id}", response_model=FolioResponse)
def get_folio(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    return build_folio_response(db, folio)


@router.get("/reservations/{reservation_id}/folio", response_model=FolioResponse)
def reservation_folio(reservation_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    if not db.get(Reservation, reservation_id): raise HTTPException(status_code=404, detail="Reservation not found")
    folio = db.scalar(select(Folio.id).where(Folio.reservation_id == reservation_id))
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    folio_obj = db.get(Folio, folio)
    return build_folio_response(db, folio_obj)


@router.get("/folios/{folio_id}/receipt")
def get_receipt(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    reservation = db.get(Reservation, folio.reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    guest = db.get(Guest, reservation.guest_id)
    if not guest: raise HTTPException(status_code=404, detail="Guest not found")
    summary = build_folio_response(db, folio)
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation.id)).all()
    rooms = [db.get(Room, room_id) for room_id in room_ids]
    return {"folio_id": folio.id, "reservation_id": reservation.id, "status": folio.status, "guest": {"full_name": guest.full_name, "phone": guest.phone, "email": guest.email, "address": guest.address}, "stay": {"check_in": reservation.check_in, "check_out": reservation.check_out, "nights": (reservation.check_out - reservation.check_in).days}, "rooms": [{"id": room.id, "number": room.number, "room_type_id": room.room_type_id} for room in rooms if room], "items": [{"id": item.id, "description": item.description, "category": item.category, "quantity": float(item.quantity), "unit_price": float(item.unit_price), "discount": float(item.discount), "line_total": float(item.line_total)} for item in summary.items], "payments": [{"id": payment.id, "amount": float(payment.amount), "method": payment.method, "reference": payment.reference} for payment in summary.payments], "refunds": [{"id": refund.id, "payment_id": refund.payment_id, "amount": float(refund.amount), "method": refund.method, "reference": refund.reference, "reason": refund.reason} for refund in db.scalars(select(PaymentRefund).where(PaymentRefund.folio_id == folio.id).order_by(PaymentRefund.id)).all()], "subtotal": float(summary.subtotal), "discounts": float(summary.discounts), "food_service_charge": float(summary.food_service_charge), "total": float(summary.total), "paid": float(summary.paid), "balance": float(summary.balance)}


@router.post("/folios/{folio_id}/room-charges", response_model=FolioResponse)
def add_room_charges(folio_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    reservation = db.get(Reservation, folio.reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    stays = db.scalars(select(Stay).where(Stay.reservation_id == reservation.id).order_by(Stay.id)).all()
    if not stays: raise HTTPException(status_code=409, detail="Reservation has no room-level stays")
    posted = 0
    for stay in stays:
        total_nights = max(0, (stay.check_out - stay.check_in).days)
        if total_nights <= 0: continue
        charged = db.scalar(select(func.coalesce(func.sum(FolioItem.quantity), 0)).where(FolioItem.folio_id == folio.id, FolioItem.stay_id == stay.id, FolioItem.category == "room")) or 0
        charged_nights = Decimal(str(charged))
        if charged_nights >= total_nights: continue
        segments = db.scalars(select(StayRateSegment).where(StayRateSegment.stay_id == stay.id).order_by(StayRateSegment.from_date, StayRateSegment.id)).all()
        if not segments:
            segments = [type("LegacyRate", (), {"from_date": stay.check_in, "to_date": stay.check_out, "rate": money(stay.agreed_rate), "discount_amount": Decimal("0.00")})()]
        remaining_to_skip = charged_nights
        remaining_nights = Decimal(total_nights) - charged_nights
        room = db.get(Room, stay.room_id)
        if not room: raise HTTPException(status_code=409, detail=f"Stay {stay.id} references an invalid room")
        for segment in segments:
            segment_nights = Decimal(max(0, (segment.to_date - segment.from_date).days))
            if segment_nights <= 0: continue
            skip = min(remaining_to_skip, segment_nights)
            remaining_to_skip -= skip
            billable = min(segment_nights - skip, remaining_nights)
            if billable <= 0: continue
            gross_rate = money(Decimal(segment.rate))
            discount_total = money(Decimal(segment.discount_amount) * billable)
            item = FolioItem(folio_id=folio.id, stay_id=stay.id, description=f"Room {room.number} · stay #{stay.id} · {int(billable)} night(s) · {segment.from_date}→{segment.to_date}", category="room", quantity=billable, unit_price=gross_rate, discount=discount_total)
            db.add(item); db.flush()
            post_folio_charge(db, folio_id=folio.id, reservation_id=reservation.id, item_id=item.id, amount=item_line_total(item), stay_id=stay.id, category=item.category, created_by=user.id, gross_amount=money(gross_rate * billable), discount_amount=discount_total)
            posted += 1
            remaining_nights -= billable
            if remaining_nights <= 0: break
    if posted:
        audit(db, user.id, "add_room_charges", "folio", folio.id, {"reservation_id": reservation.id, "stay_ids": [stay.id for stay in stays], "room_charges_posted": posted}); db.commit(); db.refresh(folio)
    return build_folio_response(db, folio)


@router.post("/folios/{folio_id}/items", response_model=FolioItemResponse, status_code=201)
def add_folio_item(folio_id: int, payload: FolioItemCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    gross = money(payload.quantity * payload.unit_price)
    if payload.discount > gross: raise HTTPException(status_code=400, detail="Discount cannot exceed the line amount")
    item = FolioItem(folio_id=folio_id, **payload.model_dump()); db.add(item); db.flush()
    reservation = db.get(Reservation, folio.reservation_id)
    post_folio_charge(db, folio_id=folio_id, reservation_id=reservation.id if reservation else 0, item_id=item.id, amount=item_line_total(item), stay_id=item.stay_id, category=item.category, created_by=user.id, gross_amount=gross, discount_amount=money(payload.discount))
    audit(db, user.id, "add", "folio_item", item.id, {"folio_id": folio_id, "description": item.description, "amount": str(item_line_total(item)), "category": item.category}); db.commit(); db.refresh(item)
    return FolioItemResponse(id=item.id, description=item.description, category=item.category, quantity=item.quantity, unit_price=item.unit_price, discount=item.discount, line_total=item_line_total(item))


@router.patch("/folios/{folio_id}/items/{item_id}", response_model=FolioItemResponse)
def update_folio_item(folio_id: int, item_id: int, payload: FolioItemUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id); item = db.get(FolioItem, item_id)
    if not folio or not item or item.folio_id != folio_id: raise HTTPException(status_code=404, detail="Folio item not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    if has_posted_folio_item_transaction(db, item.id):
        raise HTTPException(status_code=409, detail="Posted folio charges are immutable; use a ledger adjustment or reversal")
    if item.stay_id is not None: raise HTTPException(status_code=409, detail="Stay-generated room charges cannot be manually edited")
    gross = money(payload.quantity * payload.unit_price)
    if payload.discount > gross: raise HTTPException(status_code=400, detail="Discount cannot exceed the line amount")
    item.description = payload.description; item.category = payload.category; item.quantity = payload.quantity; item.unit_price = payload.unit_price; item.discount = payload.discount
    audit(db, user.id, "update", "folio_item", item.id, {"folio_id": folio_id, "to": payload.model_dump(mode="json")}); db.commit(); db.refresh(item)
    return FolioItemResponse(id=item.id, description=item.description, category=item.category, quantity=item.quantity, unit_price=item.unit_price, discount=item.discount, line_total=item_line_total(item))


@router.delete("/folios/{folio_id}/items/{item_id}", status_code=204)
def remove_folio_item(folio_id: int, item_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id); item = db.get(FolioItem, item_id)
    if not folio or not item or item.folio_id != folio_id: raise HTTPException(status_code=404, detail="Folio item not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    if has_posted_folio_item_transaction(db, item.id):
        raise HTTPException(status_code=409, detail="Posted folio charges are immutable; use a ledger adjustment or reversal")
    if item.stay_id is not None: raise HTTPException(status_code=409, detail="Stay-generated room charges cannot be manually removed")
    db.delete(item); audit(db, user.id, "remove", "folio_item", item_id, {"folio_id": folio_id}); db.commit()


@router.post("/folios/{folio_id}/payments", response_model=PaymentResponse, status_code=201)
def add_payment(folio_id: int, payload: PaymentCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    if idempotency_key:
        from .models import FinancialTransaction
        existing = db.scalar(select(FinancialTransaction).where(FinancialTransaction.idempotency_key == idempotency_key))
        if existing:
            if existing.transaction_type != "folio_payment" or existing.folio_id != folio_id:
                raise HTTPException(status_code=409, detail="Idempotency key is already bound to another financial operation")
            payment = db.get(Payment, int(existing.reference_id)) if existing.reference_id else None
            if not payment: raise HTTPException(status_code=409, detail="Idempotent payment record is missing")
            return payment
    summary = build_folio_response(db, folio)
    if payload.amount > summary.balance: raise HTTPException(status_code=400, detail=f"Payment exceeds outstanding balance of {summary.balance}")
    reservation = db.get(Reservation, folio.reservation_id)
    payment = Payment(folio_id=folio_id, amount=payload.amount, method=payload.method, reference=payload.reference); db.add(payment); db.flush()
    tx = post_folio_payment(db, folio_id=folio.id, reservation_id=reservation.id if reservation else 0, payment_id=payment.id, amount=payload.amount, method=payload.method, created_by=user.id)
    if idempotency_key and tx.idempotency_key != idempotency_key:
        raise HTTPException(status_code=409, detail="Financial idempotency key was not applied")
    audit(db, user.id, "payment", "folio", folio_id, {"amount": str(payload.amount), "method": payload.method, "reference": payload.reference}); db.commit(); db.refresh(payment)
    return payment


@router.post("/folios/{folio_id}/close", response_model=FolioResponse)
def close_folio(folio_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open": raise HTTPException(status_code=409, detail="Folio is already closed")
    summary = build_folio_response(db, folio)
    if summary.balance != 0: raise HTTPException(status_code=409, detail=f"Folio cannot be closed with an outstanding balance of {summary.balance}")
    folio.status = "closed"; audit(db, user.id, "close", "folio", folio.id, {"reservation_id": folio.reservation_id}); db.commit(); db.refresh(folio)
    return build_folio_response(db, folio)
