from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from secrets import token_hex

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .models import BusinessDateState, FinancialTransaction, LedgerEntry, User

# Mounted inside billing.py, whose router prefix is already /api.
router = APIRouter(prefix="/ledger", tags=["ledger"])
MONEY = Decimal("0.01")


class LedgerLine(BaseModel):
    account: str = Field(min_length=1, max_length=60)
    direction: str = Field(pattern="^(debit|credit)$")
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="PKR", min_length=3, max_length=3)
    folio_id: int | None = None
    stay_id: int | None = None
    payment_method: str | None = Field(default=None, max_length=30)
    reference: str | None = Field(default=None, max_length=100)


class LedgerTransactionCreate(BaseModel):
    transaction_type: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=300)
    reference_type: str | None = Field(default=None, max_length=40)
    reference_id: str | None = Field(default=None, max_length=50)
    folio_id: int | None = None
    reservation_id: int | None = None
    lines: list[LedgerLine] = Field(min_length=2)


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def current_business_date(db: Session) -> date:
    state = db.get(BusinessDateState, 1)
    if state is None:
        state = BusinessDateState(id=1, current_business_date=date.today())
        db.add(state)
        db.flush()
    return state.current_business_date


def new_transaction_no(business_date: date) -> str:
    return f"TX-{business_date.strftime('%Y%m%d')}-{token_hex(5).upper()}"


def post_transaction(
    db: Session,
    *,
    transaction_type: str,
    description: str,
    lines: list[LedgerLine | dict],
    created_by: int | None = None,
    business_date: date | None = None,
    reference_type: str | None = None,
    reference_id: str | None = None,
    folio_id: int | None = None,
    reservation_id: int | None = None,
    reversal_of_id: int | None = None,
) -> FinancialTransaction:
    if len(lines) < 2:
        raise ValueError("A financial transaction requires at least two ledger lines")

    normalized: list[dict] = []
    debits = Decimal("0.00")
    credits = Decimal("0.00")
    currency_set: set[str] = set()

    for raw in lines:
        line = raw.model_dump() if isinstance(raw, LedgerLine) else dict(raw)
        amount = money(line["amount"])
        if amount <= 0:
            raise ValueError("Ledger amounts must be greater than zero")
        direction = line["direction"]
        if direction not in {"debit", "credit"}:
            raise ValueError("Ledger direction must be debit or credit")
        currency = str(line.get("currency", "PKR")).upper()
        currency_set.add(currency)
        normalized.append({**line, "amount": amount, "currency": currency})
        if direction == "debit":
            debits += amount
        else:
            credits += amount

    if len(currency_set) != 1:
        raise ValueError("A transaction must use one currency")
    if money(debits) != money(credits):
        raise ValueError(f"Unbalanced ledger transaction: debit={money(debits)} credit={money(credits)}")

    tx_date = business_date or current_business_date(db)
    transaction = FinancialTransaction(
        transaction_no=new_transaction_no(tx_date),
        business_date=tx_date,
        transaction_type=transaction_type,
        status="posted",
        reference_type=reference_type,
        reference_id=reference_id,
        folio_id=folio_id,
        reservation_id=reservation_id,
        description=description,
        created_by=created_by,
        reversal_of_id=reversal_of_id,
    )
    db.add(transaction)
    db.flush()

    for line in normalized:
        db.add(
            LedgerEntry(
                transaction_id=transaction.id,
                account=line["account"],
                direction=line["direction"],
                amount=line["amount"],
                currency=line["currency"],
                folio_id=line.get("folio_id") or folio_id,
                stay_id=line.get("stay_id"),
                payment_method=line.get("payment_method"),
                reference=line.get("reference"),
            )
        )
    db.flush()
    return transaction


def post_folio_charge(db: Session, *, folio_id: int, reservation_id: int, item_id: int, amount: Decimal, stay_id: int | None, category: str, created_by: int) -> FinancialTransaction:
    return post_transaction(
        db,
        transaction_type="folio_charge",
        description=f"Folio charge #{item_id}: {category}",
        reference_type="folio_item",
        reference_id=str(item_id),
        folio_id=folio_id,
        reservation_id=reservation_id,
        created_by=created_by,
        lines=[
            {"account": "Guest Receivables", "direction": "debit", "amount": amount, "folio_id": folio_id, "stay_id": stay_id},
            {"account": "Revenue - " + (category[:45] or "Other"), "direction": "credit", "amount": amount, "folio_id": folio_id, "stay_id": stay_id},
        ],
    )


def post_folio_payment(db: Session, *, folio_id: int, reservation_id: int, payment_id: int, amount: Decimal, method: str, created_by: int) -> FinancialTransaction:
    account = {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(method, "Other Payment")
    return post_transaction(
        db,
        transaction_type="folio_payment",
        description=f"Folio payment #{payment_id} ({method})",
        reference_type="payment",
        reference_id=str(payment_id),
        folio_id=folio_id,
        reservation_id=reservation_id,
        created_by=created_by,
        lines=[
            {"account": account, "direction": "debit", "amount": amount, "folio_id": folio_id, "payment_method": method},
            {"account": "Guest Receivables", "direction": "credit", "amount": amount, "folio_id": folio_id, "payment_method": method},
        ],
    )


def post_deposit_received(db: Session, *, stay_id: int, folio_id: int | None, reservation_id: int, deposit_id: int, amount: Decimal, method: str | None, created_by: int) -> FinancialTransaction:
    cash_account = {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(method or "other", "Other Payment")
    return post_transaction(
        db,
        transaction_type="deposit_received",
        description=f"Deposit received #{deposit_id}",
        reference_type="deposit",
        reference_id=str(deposit_id),
        folio_id=folio_id,
        reservation_id=reservation_id,
        created_by=created_by,
        lines=[
            {"account": cash_account, "direction": "debit", "amount": amount, "folio_id": folio_id, "stay_id": stay_id, "payment_method": method},
            {"account": "Guest Deposits", "direction": "credit", "amount": amount, "folio_id": folio_id, "stay_id": stay_id, "payment_method": method},
        ],
    )


def reverse_transaction(db: Session, *, transaction_id: int, created_by: int, reason: str) -> FinancialTransaction:
    original = db.get(FinancialTransaction, transaction_id)
    if original is None:
        raise ValueError("Financial transaction not found")
    if original.status != "posted":
        raise ValueError("Only posted transactions can be reversed")
    existing_reversal = db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.reversal_of_id == transaction_id))
    if existing_reversal:
        raise ValueError("Transaction has already been reversed")

    source_lines = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == transaction_id).order_by(LedgerEntry.id)).all()
    if not source_lines:
        raise ValueError("Cannot reverse a transaction without ledger entries")
    reversed_lines = [
        {
            "account": line.account,
            "direction": "credit" if line.direction == "debit" else "debit",
            "amount": line.amount,
            "currency": line.currency,
            "folio_id": line.folio_id,
            "stay_id": line.stay_id,
            "payment_method": line.payment_method,
            "reference": line.reference,
        }
        for line in source_lines
    ]
    reversal = post_transaction(
        db,
        transaction_type="reversal",
        description=f"Reversal of {original.transaction_no}: {reason}",
        reference_type="financial_transaction",
        reference_id=str(original.id),
        folio_id=original.folio_id,
        reservation_id=original.reservation_id,
        created_by=created_by,
        reversal_of_id=original.id,
        lines=reversed_lines,
    )
    original.status = "reversed"
    db.flush()
    return reversal


@router.get("/transactions")
def list_transactions(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    limit = max(1, min(limit, 500))
    transactions = db.scalars(select(FinancialTransaction).order_by(FinancialTransaction.id.desc()).limit(limit)).all()
    result = []
    for tx in transactions:
        entries = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == tx.id).order_by(LedgerEntry.id)).all()
        result.append({
            "id": tx.id,
            "transaction_no": tx.transaction_no,
            "business_date": tx.business_date,
            "transaction_type": tx.transaction_type,
            "status": tx.status,
            "reference_type": tx.reference_type,
            "reference_id": tx.reference_id,
            "folio_id": tx.folio_id,
            "reservation_id": tx.reservation_id,
            "description": tx.description,
            "created_by": tx.created_by,
            "created_at": tx.created_at,
            "reversal_of_id": tx.reversal_of_id,
            "entries": [
                {"id": e.id, "account": e.account, "direction": e.direction, "amount": e.amount, "currency": e.currency, "folio_id": e.folio_id, "stay_id": e.stay_id, "payment_method": e.payment_method, "reference": e.reference}
                for e in entries
            ],
        })
    return result


@router.post("/transactions/{transaction_id}/reverse", status_code=201)
def reverse_transaction_endpoint(transaction_id: int, reason: str = "Correction", db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    try:
        reversal = reverse_transaction(db, transaction_id=transaction_id, created_by=user.id, reason=reason)
        db.commit()
        db.refresh(reversal)
        return {"id": reversal.id, "transaction_no": reversal.transaction_no, "reversal_of_id": reversal.reversal_of_id, "status": reversal.status}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
