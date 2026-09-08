from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_roles
from .business_date import get_current_business_date
from .db import get_db
from .ledger import post_transaction
from .models import AuditLog, DepositTransaction, Folio, LedgerEntry, Reservation, User
from .pms_core import Stay

router = APIRouter(prefix="/api", tags=["deposit-transfers"])
MONEY = Decimal("0.01")


class DepositTransferCreate(BaseModel):
    destination_stay_id: int
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=300)


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY)


def stay_deposit_balance(db: Session, stay_id: int) -> Decimal:
    credits = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join_from(LedgerEntry, __import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction,
                   LedgerEntry.transaction_id == __import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction.id)
        .where(
            LedgerEntry.stay_id == stay_id,
            LedgerEntry.account == "Guest Deposits",
            LedgerEntry.direction == "credit",
            __import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction.status == "posted",
        )
    ) or Decimal("0.00")
    debits = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join_from(LedgerEntry, __import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction,
                   LedgerEntry.transaction_id == __import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction.id)
        .where(
            LedgerEntry.stay_id == stay_id,
            LedgerEntry.account == "Guest Deposits",
            LedgerEntry.direction == "debit",
            __import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction.status == "posted",
        )
    ) or Decimal("0.00")
    return money(max(Decimal("0.00"), Decimal(credits) - Decimal(debits)))


def audit(db: Session, user_id: int, transfer_reference: str, details: dict) -> None:
    import json
    db.add(AuditLog(user_id=user_id, action="deposit_transfer", entity_type="deposit_transfer", entity_id=transfer_reference, details=json.dumps(details)))


@router.post("/stays/{stay_id}/deposit-transfers", status_code=201)
def transfer_deposit(
    stay_id: int,
    payload: DepositTransferCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    if len(key) > 100:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 100 characters or fewer")
    if stay_id == payload.destination_stay_id:
        raise HTTPException(status_code=400, detail="Source and destination stay must be different")

    source = db.get(Stay, stay_id)
    destination = db.get(Stay, payload.destination_stay_id)
    if not source or not destination:
        raise HTTPException(status_code=404, detail="Source or destination stay not found")

    source_reservation = db.get(Reservation, source.reservation_id)
    destination_reservation = db.get(Reservation, destination.reservation_id)
    if not source_reservation or not destination_reservation:
        raise HTTPException(status_code=409, detail="Source or destination reservation not found")

    source_folio = db.scalar(select(Folio).where(Folio.reservation_id == source.reservation_id))
    destination_folio = db.scalar(select(Folio).where(Folio.reservation_id == destination.reservation_id))
    if not source_folio or not destination_folio:
        raise HTTPException(status_code=409, detail="Source or destination folio not found")

    amount = money(payload.amount)
    available = stay_deposit_balance(db, source.id)
    if amount > available:
        raise HTTPException(status_code=409, detail=f"Transfer exceeds available source deposit balance of {available}")

    transfer_reference = key
    existing_tx = db.scalar(select(__import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction).where(__import__("app.models", fromlist=["FinancialTransaction"]).FinancialTransaction.idempotency_key == key))
    if existing_tx is not None:
        rows = db.scalars(select(DepositTransaction).where(DepositTransaction.reference == transfer_reference).order_by(DepositTransaction.id)).all()
        if len(rows) != 2:
            raise HTTPException(status_code=409, detail="Idempotent deposit transfer exists but its operational records are incomplete")
        if any(row.stay_id != source.id and row.stay_id != destination.id for row in rows):
            raise HTTPException(status_code=409, detail="Idempotency key is bound to an incompatible deposit transfer")
        return {
            "transaction_id": existing_tx.id,
            "status": existing_tx.status,
            "idempotency_key": key,
            "source_stay_id": source.id,
            "destination_stay_id": destination.id,
            "amount": amount,
            "source_balance": stay_deposit_balance(db, source.id),
            "destination_balance": stay_deposit_balance(db, destination.id),
            "replayed": True,
        }

    business_date = get_current_business_date(db, fallback_to_today=True)
    try:
        tx = post_transaction(
            db,
            transaction_type="deposit_transfer",
            description=f"Deposit transfer {source.id} → {destination.id}: {payload.reason}",
            reference_type="deposit_transfer",
            reference_id=transfer_reference,
            folio_id=source_folio.id,
            reservation_id=source.reservation_id,
            created_by=user.id,
            business_date=business_date,
            idempotency_key=key,
            lines=[
                {"account": "Guest Deposits", "direction": "debit", "amount": amount, "folio_id": source_folio.id, "stay_id": source.id},
                {"account": "Guest Deposits", "direction": "credit", "amount": amount, "folio_id": destination_folio.id, "stay_id": destination.id},
            ],
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Deposit transfer could not be committed safely; retry with the same Idempotency-Key") from exc

    rows = db.scalars(select(DepositTransaction).where(DepositTransaction.reference == transfer_reference).order_by(DepositTransaction.id)).all()
    if len(rows) == 2:
        return {
            "transaction_id": tx.id,
            "status": tx.status,
            "idempotency_key": key,
            "source_stay_id": source.id,
            "destination_stay_id": destination.id,
            "amount": amount,
            "source_balance": stay_deposit_balance(db, source.id),
            "destination_balance": stay_deposit_balance(db, destination.id),
            "replayed": True,
        }

    source_record = DepositTransaction(
        stay_id=source.id,
        folio_id=source_folio.id,
        transaction_type="transferred_out",
        amount=amount,
        payment_method=None,
        reference=transfer_reference,
        notes=payload.reason,
        created_by=user.id,
    )
    destination_record = DepositTransaction(
        stay_id=destination.id,
        folio_id=destination_folio.id,
        transaction_type="transferred_in",
        amount=amount,
        payment_method=None,
        reference=transfer_reference,
        notes=payload.reason,
        created_by=user.id,
    )
    db.add(source_record)
    db.add(destination_record)
    db.flush()
    source.deposit_received = stay_deposit_balance(db, source.id)
    destination.deposit_received = stay_deposit_balance(db, destination.id)
    audit(db, user.id, transfer_reference, {
        "transaction_id": tx.id,
        "source_stay_id": source.id,
        "destination_stay_id": destination.id,
        "amount": str(amount),
        "reason": payload.reason,
    })
    db.commit()
    return {
        "transaction_id": tx.id,
        "status": tx.status,
        "idempotency_key": key,
        "source_stay_id": source.id,
        "destination_stay_id": destination.id,
        "amount": amount,
        "source_balance": source.deposit_received,
        "destination_balance": destination.deposit_received,
        "source_transaction_id": source_record.id,
        "destination_transaction_id": destination_record.id,
        "replayed": False,
    }
