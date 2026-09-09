from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .financial_authority import folio_ledger_summary
from .financial_models import PaymentRefund
from .models import AuditLog, BusinessDateState, DepositTransaction, FinancialTransaction, Folio, FolioItem, LedgerEntry, Payment, Reservation, User
from .pms_core import FolioWindow, Stay

router = APIRouter(prefix="/finance", tags=["finance-controls"])
MONEY = Decimal("0.01")
CASH_ACCOUNTS = {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def current_business_date(db: Session) -> date:
    state = db.get(BusinessDateState, 1)
    return state.current_business_date if state else date.today()


def require_open_business_date(db: Session) -> date:
    state = db.get(BusinessDateState, 1)
    current = state.current_business_date if state else date.today()
    if state and state.last_closed_at is not None and state.last_closed_at.date() >= current:
        raise HTTPException(status_code=409, detail=f"Business date {current.isoformat()} is closed for posting")
    return current


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def post_to_ledger(db: Session, **kwargs):
    from .ledger import post_transaction
    return post_transaction(db, **kwargs)


class FolioTransferCreate(BaseModel):
    item_id: int
    to_folio_id: int
    reason: str = Field(min_length=1, max_length=300)


class DepositPostCreate(BaseModel):
    transaction_type: str = Field(pattern="^(received|applied|refunded)$")
    amount: Decimal = Field(gt=0)
    payment_method: str | None = Field(default=None, max_length=30)
    reference: str | None = Field(default=None, max_length=100)
    notes: str | None = None


@router.get("/period")
def period_status(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    current = state.current_business_date if state else date.today()
    closed = bool(state and state.last_closed_at and state.last_closed_at.date() >= current)
    return {"business_date": current, "opened_at": state.opened_at if state else None, "last_closed_at": state.last_closed_at if state else None, "posting_open": not closed}


@router.post("/folio-transfers", status_code=201)
def transfer_folio_item(payload: FolioTransferCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    require_open_business_date(db)
    item = db.get(FolioItem, payload.item_id); destination = db.get(Folio, payload.to_folio_id)
    if not item or not destination: raise HTTPException(status_code=404, detail="Folio item or destination folio not found")
    source = db.get(Folio, item.folio_id)
    if not source: raise HTTPException(status_code=409, detail="Source folio not found")
    if source.id == destination.id: raise HTTPException(status_code=400, detail="Source and destination folios must be different")
    if source.status != "open" or destination.status != "open": raise HTTPException(status_code=409, detail="Both source and destination folios must be open")
    amount = money(max(Decimal("0.00"), Decimal(item.quantity) * Decimal(item.unit_price) - Decimal(item.discount)))
    source_id, destination_id = source.id, destination.id
    item.folio_id = destination_id; db.flush()
    post_to_ledger(db, transaction_type="folio_transfer", description=f"Transfer folio item #{item.id}: {source_id} -> {destination_id}", reference_type="folio_item", reference_id=str(item.id), folio_id=destination_id, reservation_id=destination.reservation_id, created_by=user.id, lines=[{"account": "Guest Receivables", "direction": "debit", "amount": amount, "folio_id": destination_id}, {"account": "Guest Receivables", "direction": "credit", "amount": amount, "folio_id": source_id}])
    audit(db, user.id, "transfer", "folio_item", item.id, {"from_folio_id": source_id, "to_folio_id": destination_id, "amount": str(amount), "reason": payload.reason})
    db.commit(); return {"item_id": item.id, "from_folio_id": source_id, "to_folio_id": destination_id, "amount": amount, "reason": payload.reason}


@router.post("/stays/{stay_id}/deposit-transactions", status_code=201)
def post_deposit(stay_id: int, payload: DepositPostCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    business_date = require_open_business_date(db)
    stay = db.get(Stay, stay_id)
    if not stay: raise HTTPException(status_code=404, detail="Stay not found")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == stay.reservation_id)); reservation = db.get(Reservation, stay.reservation_id)
    current = _stay_deposit_ledger_balance(db, stay_id)
    if payload.transaction_type == "received":
        new_balance = money(current + payload.amount)
        if stay.deposit_required > 0 and new_balance > stay.deposit_required: raise HTTPException(status_code=409, detail="Deposit received exceeds required deposit")
    else:
        new_balance = money(current - payload.amount)
        if new_balance < 0: raise HTTPException(status_code=409, detail="Deposit transaction exceeds available deposit balance")
    tx = DepositTransaction(stay_id=stay_id, folio_id=folio.id if folio else None, transaction_type=payload.transaction_type, amount=money(payload.amount), payment_method=payload.payment_method, reference=payload.reference, notes=payload.notes, created_by=user.id)
    db.add(tx); db.flush()
    if payload.transaction_type == "received":
        account = CASH_ACCOUNTS.get(payload.payment_method or "other", "Other Payment")
        lines = [{"account": account, "direction": "debit", "amount": tx.amount, "folio_id": tx.folio_id, "stay_id": stay_id, "payment_method": payload.payment_method}, {"account": "Guest Deposits", "direction": "credit", "amount": tx.amount, "folio_id": tx.folio_id, "stay_id": stay_id, "payment_method": payload.payment_method}]
    elif payload.transaction_type == "applied":
        lines = [{"account": "Guest Deposits", "direction": "debit", "amount": tx.amount, "folio_id": tx.folio_id, "stay_id": stay_id}, {"account": "Guest Receivables", "direction": "credit", "amount": tx.amount, "folio_id": tx.folio_id, "stay_id": stay_id}]
    else:
        account = CASH_ACCOUNTS.get(payload.payment_method or "other", "Other Payment")
        lines = [{"account": "Guest Deposits", "direction": "debit", "amount": tx.amount, "folio_id": tx.folio_id, "stay_id": stay_id}, {"account": account, "direction": "credit", "amount": tx.amount, "folio_id": tx.folio_id, "stay_id": stay_id, "payment_method": payload.payment_method}]
    post_to_ledger(db, transaction_type=f"deposit_{payload.transaction_type}", description=f"Deposit {payload.transaction_type} #{tx.id}", reference_type="deposit", reference_id=str(tx.id), folio_id=tx.folio_id, reservation_id=reservation.id if reservation else None, created_by=user.id, business_date=business_date, lines=lines)
    stay.deposit_received = _stay_deposit_ledger_balance(db, stay_id)
    audit(db, user.id, "deposit", "stay", stay_id, {"deposit_id": tx.id, "transaction_type": payload.transaction_type, "amount": str(tx.amount), "new_balance": str(stay.deposit_received)})
    db.commit(); db.refresh(tx)
    return {"id": tx.id, "stay_id": stay_id, "transaction_type": tx.transaction_type, "amount": tx.amount, "balance": stay.deposit_received}


@router.get("/stays/{stay_id}/deposit-ledger")
def deposit_ledger(stay_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    if not db.get(Stay, stay_id): raise HTTPException(status_code=404, detail="Stay not found")
    rows = db.scalars(select(DepositTransaction).where(DepositTransaction.stay_id == stay_id).order_by(DepositTransaction.created_at, DepositTransaction.id)).all()
    balance = Decimal("0.00"); result = []
    for row in rows:
        balance += row.amount if row.transaction_type in {"received", "adjusted"} else -row.amount
        result.append({"id": row.id, "transaction_type": row.transaction_type, "amount": row.amount, "payment_method": row.payment_method, "reference": row.reference, "notes": row.notes, "created_at": row.created_at, "balance": money(balance)})
    return {"stay_id": stay_id, "balance": _stay_deposit_ledger_balance(db, stay_id), "transactions": result}


def _stay_deposit_ledger_balance(db: Session, stay_id: int) -> Decimal:
    credits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == stay_id, LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted")) or Decimal("0.00")
    debits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == stay_id, LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted")) or Decimal("0.00")
    return money(max(Decimal("0.00"), Decimal(credits) - Decimal(debits)))


@router.get("/reports/trial-balance")
def trial_balance(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    target = business_date or current_business_date(db)
    rows = db.execute(select(LedgerEntry.account, LedgerEntry.direction, func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(FinancialTransaction.business_date == target, FinancialTransaction.status.in_(("posted", "reversed"))).group_by(LedgerEntry.account, LedgerEntry.direction).order_by(LedgerEntry.account, LedgerEntry.direction)).all()
    accounts: dict[str, dict[str, Decimal]] = {}
    for account, direction, amount in rows: accounts.setdefault(account, {"debit": Decimal("0.00"), "credit": Decimal("0.00")})[direction] = money(amount)
    result = []; total_debit = Decimal("0.00"); total_credit = Decimal("0.00")
    for account, values in accounts.items():
        total_debit += values["debit"]; total_credit += values["credit"]; result.append({"account": account, "debit": money(values["debit"]), "credit": money(values["credit"]), "net": money(values["debit"] - values["credit"])})
    return {"business_date": target, "balanced": money(total_debit) == money(total_credit), "total_debit": money(total_debit), "total_credit": money(total_credit), "accounts": result}


@router.get("/reports/payment-reconciliation")
def payment_reconciliation(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    target = business_date or current_business_date(db)
    transactions = db.scalars(select(FinancialTransaction).where(FinancialTransaction.business_date == target, FinancialTransaction.status == "posted", FinancialTransaction.transaction_type.in_(("folio_payment", "payment_refund")))).all()
    received: dict[str, Decimal] = {}; refunded: dict[str, Decimal] = {}
    for tx in transactions:
        entries = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id)).all(); cash_entries = [entry for entry in entries if entry.account in {"Cash", "Card Clearing", "Bank", "Other Payment"}]; amount = money(sum((entry.amount for entry in cash_entries), Decimal("0.00"))); method = next((entry.payment_method for entry in cash_entries if entry.payment_method), "other"); destination = refunded if tx.transaction_type == "payment_refund" else received; destination[method] = destination.get(method, Decimal("0.00")) + amount
    methods = sorted(set(received) | set(refunded)); rows = [{"method": method, "received": money(received.get(method, 0)), "refunded": money(refunded.get(method, 0)), "net": money(received.get(method, 0) - refunded.get(method, 0))} for method in methods]
    return {"business_date": target, "methods": rows, "received_total": money(sum(received.values(), Decimal("0.00"))), "refunded_total": money(sum(refunded.values(), Decimal("0.00"))), "net_total": money(sum(received.values(), Decimal("0.00")) - sum(refunded.values(), Decimal("0.00")))}


@router.get("/reports/revenue")
def revenue_report(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    target = business_date or current_business_date(db)
    rows = db.execute(select(LedgerEntry.account, func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(FinancialTransaction.business_date == target, FinancialTransaction.status == "posted", LedgerEntry.direction == "credit", LedgerEntry.account.like("Revenue - %")).group_by(LedgerEntry.account).order_by(LedgerEntry.account)).all()
    lines = [{"account": account, "amount": money(amount)} for account, amount in rows]
    return {"business_date": target, "revenue": lines, "total": money(sum((line["amount"] for line in lines), Decimal("0.00")))}


@router.get("/reports/accounts-receivable")
def accounts_receivable(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    result = []; total = Decimal("0.00")
    for folio in db.scalars(select(Folio).order_by(Folio.id)).all():
        balance = folio_ledger_summary(db, folio.id).balance
        if balance > 0:
            total += balance
            result.append({"folio_id": folio.id, "reservation_id": folio.reservation_id, "balance": balance, "status": folio.status})
    result.sort(key=lambda row: row["balance"], reverse=True)
    return {"total_outstanding": money(total), "folios": result}
