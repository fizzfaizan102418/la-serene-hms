from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .business_date import get_current_business_date
from .financial_authority import post_folio_charge_authoritative
from .models import Folio, FolioItem, Reservation, Room, User


def _require_open_folio(db: Session, folio_id: int) -> Folio:
    folio = db.get(Folio, folio_id)
    if folio is None:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is closed")
    return folio


def post_restaurant_charge(
    db: Session,
    *,
    folio_id: int,
    description: str,
    quantity: Decimal,
    unit_price: Decimal,
    service_charge_rate: Decimal = Decimal("0"),
    user: User | None = None,
) -> FolioItem:
    if quantity <= 0 or unit_price < 0:
        raise HTTPException(status_code=422, detail="Quantity must be positive and price cannot be negative")
    if service_charge_rate < 0:
        raise HTTPException(status_code=422, detail="Service charge rate cannot be negative")

    folio = _require_open_folio(db, folio_id)
    business_date = get_current_business_date(db)
    net = (quantity * unit_price).quantize(Decimal("0.01"))
    service_charge = (net * service_charge_rate / Decimal("100")).quantize(Decimal("0.01"))
    gross = net + service_charge

    item = FolioItem(
        folio_id=folio.id,
        description=description.strip() or "Restaurant charge",
        quantity=quantity,
        unit_price=unit_price,
        total=gross,
        business_date=business_date,
        category="food",
    )
    db.add(item)
    db.flush()

    post_folio_charge_authoritative(
        db,
        folio=folio,
        amount=gross,
        business_date=business_date,
        description=item.description,
        user_id=user.id if user else None,
    )
    return item
