from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .financial_authority import post_folio_charge_authoritative
from .ledger import reverse_transaction
from .models import AuditLog, FinancialTransaction, Folio, FolioItem, Reservation, User
from .schemas import FolioItemResponse

router = APIRouter(prefix="", tags=["folio-corrections"])
MONEY = Decimal("0.01")
ITEM_TRANSACTION_REFERENCES = {"folio_item", "folio_item_discount", "folio_item_service_charge"}


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def item_line_total(item: FolioItem) -> Decimal:
    gross = Decimal(item.quantity) * Decimal(item.unit_price)
    return money(max(Decimal("0.00"), gross - Decimal(item.discount)))


def audit(db: Session, user_id: int, action: str, entity_id: int, details: dict) -> None:
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type="folio_item", entity_id=str(entity_id), details=json.dumps(details)))


class FolioItemCorrection(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=50)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    discount: Decimal = Field(default=0, ge=0)
    reason: str = Field(default="Billing correction", min_length=1, max_length=300)


class FolioItemCorrectionResponse(BaseModel):
    action: str
    original_item_id: int
    replacement_item: FolioItemResponse | None = None
    reversed_transaction_ids: list[int]


def _posted_item_transactions(db: Session, item_id: int) -> list[FinancialTransaction]:
    return db.scalars(
        select(FinancialTransaction)
        .where(
            FinancialTransaction.reference_id == str(item_id),
            FinancialTransaction.reference_type.in_(ITEM_TRANSACTION_REFERENCES),
            FinancialTransaction.status == "posted",
        )
        .order_by(FinancialTransaction.id)
    ).all()


def _validate_target(db: Session, folio_id: int, item_id: int) -> tuple[Folio, FolioItem, Reservation]:
    folio = db.get(Folio, folio_id)
    item = db.get(FolioItem, item_id)
    if not folio or not item or item.folio_id != folio_id:
        raise HTTPException(status_code=404, detail="Folio item not found")
    if folio.status != "open":
        raise HTTPException(status_code=409, detail="Folio is already closed")
    reservation = db.get(Reservation, folio.reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return folio, item, reservation


@router.post("/folios/{folio_id}/items/{item_id}/reverse", response_model=FolioItemCorrectionResponse, status_code=201)
def reverse_folio_item(
    folio_id: int,
    item_id: int,
    reason: str = "Billing correction",
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    folio, item, _ = _validate_target(db, folio_id, item_id)
    transactions = _posted_item_transactions(db, item.id)
    if not transactions:
        raise HTTPException(status_code=409, detail="Folio charge is already reversed or has no posted financial transactions")
    transaction_ids = [tx.id for tx in transactions]
    try:
        for tx in transactions:
            reverse_transaction(db, transaction_id=tx.id, created_by=user.id, reason=reason)
        audit(db, user.id, "reverse", item.id, {"folio_id": folio_id, "reason": reason, "transaction_ids": transaction_ids})
        db.commit()
    except (ValueError, HTTPException) as exc:
        db.rollback()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FolioItemCorrectionResponse(action="reversed", original_item_id=item.id, reversed_transaction_ids=transaction_ids)


@router.post("/folios/{folio_id}/items/{item_id}/correct", response_model=FolioItemCorrectionResponse, status_code=201)
def correct_folio_item(
    folio_id: int,
    item_id: int,
    payload: FolioItemCorrection,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin")),
):
    folio, item, reservation = _validate_target(db, folio_id, item_id)
    gross = money(payload.quantity * payload.unit_price)
    if payload.discount > gross:
        raise HTTPException(status_code=400, detail="Discount cannot exceed the line amount")
    transactions = _posted_item_transactions(db, item.id)
    if not transactions:
        raise HTTPException(status_code=409, detail="Folio charge is already reversed or has no posted financial transactions")
    transaction_ids = [tx.id for tx in transactions]
    try:
        for tx in transactions:
            reverse_transaction(db, transaction_id=tx.id, created_by=user.id, reason=payload.reason)
        replacement = FolioItem(
            folio_id=folio_id,
            stay_id=item.stay_id,
            description=payload.description,
            category=payload.category,
            quantity=payload.quantity,
            unit_price=payload.unit_price,
            discount=payload.discount,
        )
        db.add(replacement)
        db.flush()
        post_folio_charge_authoritative(
            db,
            folio_id=folio_id,
            reservation_id=reservation.id,
            item_id=replacement.id,
            amount=item_line_total(replacement),
            stay_id=replacement.stay_id,
            category=replacement.category,
            created_by=user.id,
            gross_amount=gross,
            discount_amount=money(payload.discount),
        )
        audit(
            db,
            user.id,
            "correct",
            item.id,
            {
                "folio_id": folio_id,
                "reason": payload.reason,
                "reversed_transaction_ids": transaction_ids,
                "replacement_item_id": replacement.id,
                "replacement": payload.model_dump(mode="json"),
            },
        )
        db.commit()
        db.refresh(replacement)
    except (ValueError, HTTPException) as exc:
        db.rollback()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FolioItemCorrectionResponse(
        action="corrected",
        original_item_id=item.id,
        replacement_item=FolioItemResponse(
            id=replacement.id,
            description=replacement.description,
            category=replacement.category,
            quantity=replacement.quantity,
            unit_price=replacement.unit_price,
            discount=replacement.discount,
            line_total=item_line_total(replacement),
        ),
        reversed_transaction_ids=transaction_ids,
    )
