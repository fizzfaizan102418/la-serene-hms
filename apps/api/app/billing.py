from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import Folio, FolioItem, Guest, Payment, Reservation, User
from .schemas import BillingSummaryResponse, FolioItemCreate, FolioItemResponse, FolioResponse, PaymentCreate, PaymentResponse

router = APIRouter(prefix="/api", tags=["billing"])
MONEY = Decimal("0.01")


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
        id=folio.id,
        reservation_id=folio.reservation_id,
        status=folio.status,
        items=[
            FolioItemResponse(
                id=i.id,
                description=i.description,
                category=i.category,
                quantity=i.quantity,
                unit_price=i.unit_price,
                discount=i.discount,
                line_total=item_line_total(i),
            )
            for i in items
        ],
        payments=[PaymentResponse.model_validate(p) for p in payments],
        subtotal=subtotal,
        discounts=discounts,
        total=total,
        paid=paid,
        balance=balance,
    )


@router.get("/billing", response_model=list[BillingSummaryResponse])
def list_billing(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    rows = db.execute(
        select(Folio, Reservation, Guest.full_name)
        .join(Reservation, Reservation.id == Folio.reservation_id)
        .join(Guest, Guest.id == Reservation.guest_id)
        .order_by(Folio.id.desc())
    ).all()
    result = []
    for folio, reservation, guest_name in rows:
        summary = build_folio_response(db, folio)
        result.append(
            BillingSummaryResponse(
                folio_id=folio.id,
                reservation_id=reservation.id,
                guest_name=guest_name,
                status=folio.status,
                total=summary.total,
                paid=summary.paid,
                balance=summary.balance,
            )
        )
    return result


@router.get("/folios/{folio_id}", response_model=FolioResponse)
def get_folio(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    return build_folio_response(db, folio)


@router.get("/reservations/{reservation_id}/folio", response_model=FolioResponse)
def reservation_folio(reservation_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    if not db.get(Reservation, reservation_id):
        raise HTTPException(status_code=404, detail="Reservation not found")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == reservation_id))
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    return build_folio_response(db, folio)


@router.post("/folios/{folio_id}/items", response_model=FolioItemResponse, status_code=201)
def add_folio_item(folio_id: int, payload: FolioItemCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is already closed")
    gross = money(payload.quantity * payload.unit_price)
    if payload.discount > gross:
        raise HTTPException(status_code=400, detail="Discount cannot exceed the line amount")
    item = FolioItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return FolioItemResponse(**payload.model_dump(), id=item.id, line_total=item_line_total(item))


@router.delete("/folios/{folio_id}/items/{item_id}", status_code=204)
def remove_folio_item(folio_id: int, item_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    item = db.get(FolioItem, item_id)
    if not folio or not item or item.folio_id != folio_id:
        raise HTTPException(status_code=404, detail="Folio item not found")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is already closed")
    db.delete(item)
    db.commit()


@router.post("/folios/{folio_id}/payments", response_model=PaymentResponse, status_code=201)
def add_payment(folio_id: int, payload: PaymentCreate, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is already closed")
    summary = build_folio_response(db, folio)
    if payload.amount > summary.balance:
        raise HTTPException(status_code=400, detail=f"Payment exceeds outstanding balance of {summary.balance}")
    payment = Payment(folio_id=folio_id, amount=money(payload.amount), method=payload.method, reference=payload.reference)
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.post("/folios/{folio_id}/close", response_model=FolioResponse)
def close_folio(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is already closed")
    summary = build_folio_response(db, folio)
    if summary.balance != Decimal("0.00"):
        raise HTTPException(status_code=409, detail=f"Cannot close folio with outstanding balance of {summary.balance}")
    folio.status = "closed"
    db.commit()
    db.refresh(folio)
    return build_folio_response(db, folio)
