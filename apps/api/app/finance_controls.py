from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .financial_models import PaymentRefund
from .ledger import post_transaction
from .models import BusinessDateState, FinancialTransaction, Folio, FolioItem, LedgerEntry, Payment, User
from .pms_core import FolioWindow

router = APIRouter(prefix="/finance", tags=["finance-controls"])
MONEY = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def require_open_business_date(db: Session, business_date: date | None = None) -> date:
    state = db.get(BusinessDateState, 1)
    current = state.current_business_date if state else date.today()
    target = business_date or current
    if target != current:
        raise HTTPException(status_code=409, detail=f"Business date {current.isoformat()} is open; financial postings cannot be backdated or future-dated")
    if state and state.last_closed_at is not None:
        raise HTTPException(status_code=409, detail="Current business date is closed")
    return current


class FolioTransferCreate(BaseModel):
    item_id: int
    to_folio_id: int
    reason: str = Field(min_length=1, max_length=300)


@router.get("/period")
def period_status(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    return {
        "business_date": state.current_business_date if state else date.today(),
        "opened_at": state.opened_at if state else None,
        "last_closed_at": state.last_closed_at if state else None,
        "posting_open": not bool(state and state.last_closed_at),
    }


@router.post("/folio-transfers", status_code=201)
def transfer_folio_item(payload: FolioTransferCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    require_open_business_date(db)
    item = db.get(FolioItem, payload.item_id)
    destination = db.get(Folio, payload.to_folio_id)
    if not item or not destination:
        raise HTTPException(status_code=404, detail="Folio item or destination folio not found")
    source = db.get(Folio, item.folio_id)
    if not source:
        raise HTTPException(status_code=409, detail="Source folio not found")
    if source.id == destination.id:
        raise HTTPException(status_code=400, detail="Source and destination folios must be different")
    if source.status != "open" or destination.status != "open":
        raise HTTPException(status_code=409, detail="Both source and destination folios must be open")

    amount = money(max(Decimal("0.00"), Decimal(item.quantity) * Decimal(item.unit_price) - Decimal(item.discount)))
    reservation_source = db.get(type(destination.reservation), source.reservation_id) if False else None
    source_reservation_id = source.reservation_id
    destination_reservation_id = destination.reservation_id

    item.folio_id = destination.id
    db.flush()
    post_transaction(
        db,
        transaction_type="folio_transfer",
        description=f"Transfer folio item #{item.id}: {source.id} -> {destination.id}",
        reference_type="folio_item",
        reference_id=str(item.id),
        folio_id=destination.id,
        reservation_id=destination_reservation_id,
        created_by=user.id,
        lines=[
            {"account": "Guest Receivables", "direction": "debit", "amount": amount, "folio_id": destination.id},
            {"account": "Guest Receivables", "direction": "credit", "amount": amount, "folio_id": source.id},
        ],
    )
    from .models import AuditLog
    import json
    db.add(AuditLog(user_id=user.id, action="transfer", entity_type="folio_item", entity_id=str(item.id), details=json.dumps({"from_folio_id": source.id, "to_folio_id": destination.id, "amount": str(amount), "reason": payload.reason})))
    db.commit()
    return {"item_id": item.id, "from_folio_id": source.id, "to_folio_id": destination.id, "amount": amount, "reason": payload.reason}


@router.get("/reports/trial-balance")
def trial_balance(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    target = business_date or (state.current_business_date if state else date.today())
    rows = db.execute(
        select(LedgerEntry.account, LedgerEntry.direction, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(FinancialTransaction.business_date == target, FinancialTransaction.status.in_(("posted", "reversed")))
        .group_by(LedgerEntry.account, LedgerEntry.direction)
        .order_by(LedgerEntry.account, LedgerEntry.direction)
    ).all()
    accounts: dict[str, dict[str, Decimal]] = {}
    for account, direction, amount in rows:
        accounts.setdefault(account, {"debit": Decimal("0.00"), "credit": Decimal("0.00")})[direction] = money(amount)
    result = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    for account, values in accounts.items():
        total_debit += values["debit"]
        total_credit += values["credit"]
        result.append({"account": account, "debit": money(values["debit"]), "credit": money(values["credit"]), "net": money(values["debit"] - values["credit"])})
    return {"business_date": target, "balanced": money(total_debit) == money(total_credit), "total_debit": money(total_debit), "total_credit": money(total_credit), "accounts": result}


@router.get("/reports/payment-reconciliation")
def payment_reconciliation(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    target = business_date or (state.current_business_date if state else date.today())
    transactions = db.scalars(select(FinancialTransaction).where(FinancialTransaction.business_date == target, FinancialTransaction.status == "posted", FinancialTransaction.transaction_type.in_(("folio_payment", "payment_refund")))).all()
    received: dict[str, Decimal] = {}
    refunded: dict[str, Decimal] = {}
    for tx in transactions:
        entries = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)).all()
        cash_entries = [e for e in entries if e.account in {"Cash", "Card Clearing", "Bank", "Other Payment"}]
        amount = money(sum((e.amount for e in cash_entries), Decimal("0.00")))
        method = next((e.payment_method for e in cash_entries if e.payment_method), "other")
        target_map = refunded if tx.transaction_type == "payment_refund" else received
        target_map[method] = target_map.get(method, Decimal("0.00")) + amount
    methods = sorted(set(received) | set(refunded))
    rows = [{"method": m, "received": money(received.get(m, 0)), "refunded": money(refunded.get(m, 0)), "net": money(received.get(m, 0) - refunded.get(m, 0))} for m in methods]
    return {"business_date": target, "methods": rows, "received_total": money(sum(received.values(), Decimal("0.00"))), "refunded_total": money(sum(refunded.values(), Decimal("0.00"))), "net_total": money(sum(received.values(), Decimal("0.00")) - sum(refunded.values(), Decimal("0.00")))}


@router.get("/reports/revenue")
def revenue_report(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    target = business_date or (state.current_business_date if state else date.today())
    rows = db.execute(
        select(LedgerEntry.account, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(FinancialTransaction.business_date == target, FinancialTransaction.status == "posted", LedgerEntry.direction == "credit", LedgerEntry.account.like("Revenue - %"))
        .group_by(LedgerEntry.account)
        .order_by(LedgerEntry.account)
    ).all()
    lines = [{"account": account, "amount": money(amount)} for account, amount in rows]
    return {"business_date": target, "revenue": lines, "total": money(sum((row["amount"] for row in lines), Decimal("0.00")))}


@router.get("/reports/accounts-receivable")
def accounts_receivable(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    folios = db.scalars(select(Folio)).all()
    result = []
    total = Decimal("0.00")
    for folio in folios:
        charges = db.scalar(select(func.coalesce(func.sum(FolioItem.quantity * FolioItem.unit_price - FolioItem.discount), 0)).where(FolioItem.folio_id == folio.id)) or 0
        payments = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.folio_id == folio.id)) or 0
        refunds = db.scalar(select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(PaymentRefund.folio_id == folio.id)) or 0
        balance = money(max(Decimal("0.00"), Decimal(charges) - Decimal(payments) + Decimal(refunds)))
        if balance > 0:
            total += balance
            result.append({"folio_id": folio.id, "reservation_id": folio.reservation_id, "balance": balance, "status": folio.status})
    result.sort(key=lambda row: row["balance"], reverse=True)
    return {"total_outstanding": money(total), "folios": result}
