from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_roles
from .business_date import get_current_business_date
from .db import get_db
from .financial_models import FolioItemWindow, Invoice, InvoiceSequence, PaymentRefund
from .ledger import post_deposit_received, post_transaction
from .models import AuditLog, BusinessDateState, DepositTransaction, FinancialTransaction, Folio, FolioItem, Guest, LedgerEntry, Payment, Reservation, ReservationRoom, Room, User
from .pms_core import FolioWindow, Stay

router = APIRouter(prefix="", tags=["financial-operations"])
MONEY = Decimal("0.01")
FOOD_CATEGORIES = {"food", "restaurant", "room_service", "beverage", "drink", "snack"}
CASH_ACCOUNTS = {"Cash", "Card Clearing", "Bank", "Other Payment"}


class RefundCreate(BaseModel):
    payment_id: int
    amount: Decimal = Field(gt=0)
    method: str | None = Field(default=None, max_length=30)
    reference: str | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=1, max_length=300)


class AdjustmentCreate(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    folio_id: int | None = None
    reservation_id: int | None = None
    lines: list[dict] = Field(min_length=2)


class WindowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    payer_type: str = Field(default="guest", max_length=30)
    guest_id: int | None = None
    group_id: int | None = None


class WindowTransfer(BaseModel):
    item_id: int


class CheckoutResponse(BaseModel):
    id: int
    status: str
    folio_id: int
    room_ids: list[int]


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def item_total(item: FolioItem) -> Decimal:
    return money(max(Decimal("0.00"), Decimal(item.quantity) * Decimal(item.unit_price) - Decimal(item.discount)))


def food_service_charge(items: list[FolioItem]) -> Decimal:
    food_net = sum((item_total(i) for i in items if i.category.strip().lower() in FOOD_CATEGORIES), Decimal("0.00"))
    return money(food_net * Decimal("0.10"))


def folio_balance(db: Session, folio: Folio) -> tuple[Decimal, Decimal, Decimal]:
    summary = __import__("app.financial_authority", fromlist=["folio_ledger_summary"]).folio_ledger_summary(db, folio.id)
    return summary.total, summary.paid, summary.balance


def stay_deposit_ledger_balance(db: Session, stay_id: int) -> Decimal:
    credits = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.stay_id == stay_id,
            LedgerEntry.account == "Guest Deposits",
            LedgerEntry.direction == "credit",
            FinancialTransaction.status == "posted",
        )
    ) or Decimal("0.00")
    debits = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.stay_id == stay_id,
            LedgerEntry.account == "Guest Deposits",
            LedgerEntry.direction == "debit",
            FinancialTransaction.status == "posted",
        )
    ) or Decimal("0.00")
    return money(max(Decimal("0.00"), Decimal(credits) - Decimal(debits)))


def payment_refunded_amount(db: Session, payment_id: int) -> Decimal:
    refund_ids = db.scalars(select(PaymentRefund.id).where(PaymentRefund.payment_id == payment_id)).all()
    if not refund_ids:
        return Decimal("0.00")
    value = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            FinancialTransaction.transaction_type == "payment_refund",
            FinancialTransaction.status == "posted",
            FinancialTransaction.reference_type == "payment_refund",
            FinancialTransaction.reference_id.in_([str(refund_id) for refund_id in refund_ids]),
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "debit",
        )
    ) or Decimal("0.00")
    return money(value)


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    import json
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


def business_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time())
    return start, start + timedelta(days=1)


def _refund_response(db: Session, folio: Folio, refund: PaymentRefund, replayed: bool) -> dict:
    _, paid_net, balance = folio_balance(db, folio)
    return {
        "id": refund.id,
        "payment_id": refund.payment_id,
        "folio_id": refund.folio_id,
        "amount": refund.amount,
        "method": refund.method,
        "paid_net": paid_net,
        "balance": balance,
        "replayed": replayed,
    }


@router.post("/reservations/{reservation_id}/check-out", response_model=CheckoutResponse)
def atomic_checkout(reservation_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation: raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in": raise HTTPException(status_code=409, detail="Reservation is not checked in")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == reservation_id))
    if not folio: raise HTTPException(status_code=409, detail="Reservation has no folio")
    total, paid, balance = folio_balance(db, folio)
    if balance != Decimal("0.00"): raise HTTPException(status_code=409, detail=f"Cannot check out with outstanding balance of {balance}")
    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation_id)).all()
    rooms = [db.get(Room, rid) for rid in room_ids]
    reservation.status = "checked_out"
    now = datetime.utcnow()
    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation_id)).all():
        stay.status = "completed"; stay.actual_check_out = stay.actual_check_out or now
    if folio.status == "open": folio.status = "closed"
    for room in rooms:
        if room and room.status == "occupied": room.status = "dirty"
    audit(db, user.id, "atomic_checkout", "reservation", reservation.id, {"folio_id": folio.id, "total": str(total), "paid": str(paid), "room_ids": room_ids})
    db.commit()
    return CheckoutResponse(id=reservation.id, status=reservation.status, folio_id=folio.id, room_ids=room_ids)


@router.post("/folios/{folio_id}/refunds", status_code=201)
def refund_payment(
    folio_id: int,
    payload: RefundCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    folio = db.get(Folio, folio_id)
    payment = db.get(Payment, payload.payment_id)
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for refunds")
    if len(key) > 100:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 100 characters or fewer")
    if not folio or not payment or payment.folio_id != folio_id:
        raise HTTPException(status_code=404, detail="Folio or payment not found")

    existing_tx = db.scalar(select(FinancialTransaction).where(FinancialTransaction.idempotency_key == key))
    if existing_tx is not None:
        if existing_tx.transaction_type != "payment_refund" or existing_tx.folio_id != folio_id:
            raise HTTPException(status_code=409, detail="Idempotency key is already bound to another financial operation")
        refund_id = int(existing_tx.reference_id) if existing_tx.reference_id and existing_tx.reference_id.isdigit() else None
        refund = db.get(PaymentRefund, refund_id) if refund_id else None
        if refund is None or refund.payment_id != payment.id:
            raise HTTPException(status_code=409, detail="Idempotent refund record is missing or incompatible")
        if refund.amount != money(payload.amount) or (refund.method or payment.method) != (payload.method or payment.method) or refund.reason != payload.reason:
            raise HTTPException(status_code=409, detail="Idempotency key is already bound to different refund parameters")
        return _refund_response(db, folio, refund, True)

    already_refunded = payment_refunded_amount(db, payment.id)
    refundable = money(Decimal(payment.amount) - already_refunded)
    amount = money(payload.amount)
    if amount > refundable:
        raise HTTPException(status_code=409, detail=f"Refund exceeds refundable payment balance of {refundable}")
    reservation = db.get(Reservation, folio.reservation_id)
    method = payload.method or payment.method
    refund = PaymentRefund(
        payment_id=payment.id,
        folio_id=folio_id,
        amount=amount,
        method=method,
        reference=payload.reference,
        reason=payload.reason,
        created_by=user.id,
    )
    db.add(refund)
    db.flush()
    cash_account = {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(method, "Other Payment")
    try:
        tx = post_transaction(
            db,
            transaction_type="payment_refund",
            description=f"Refund payment #{payment.id}",
            reference_type="payment_refund",
            reference_id=str(refund.id),
            folio_id=folio_id,
            reservation_id=reservation.id if reservation else None,
            created_by=user.id,
            idempotency_key=key,
            lines=[
                {"account": "Guest Receivables", "direction": "debit", "amount": amount, "folio_id": folio_id},
                {"account": cash_account, "direction": "credit", "amount": amount, "folio_id": folio_id, "payment_method": method},
            ],
        )
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if tx.reference_id != str(refund.id):
        db.delete(refund)
        db.flush()
        replay_refund = db.get(PaymentRefund, int(tx.reference_id)) if tx.reference_id and tx.reference_id.isdigit() else None
        if replay_refund is None or replay_refund.payment_id != payment.id:
            db.rollback()
            raise HTTPException(status_code=409, detail="Idempotent refund record is missing or incompatible")
        return _refund_response(db, folio, replay_refund, True)
    audit(db, user.id, "refund", "payment", payment.id, {"folio_id": folio_id, "refund_id": refund.id, "amount": str(amount), "method": method, "reason": payload.reason})
    db.commit()
    db.refresh(refund)
    return _refund_response(db, folio, refund, False)


@router.post("/ledger/adjustments", status_code=201)
def create_adjustment(payload: AdjustmentCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    try:
        tx = post_transaction(db, transaction_type="adjustment", description=payload.description, reference_type="manual_adjustment", reference_id=payload.description[:50], folio_id=payload.folio_id, reservation_id=payload.reservation_id, created_by=user.id, lines=payload.lines)
        audit(db, user.id, "adjustment", "financial_transaction", tx.id, {"description": payload.description, "folio_id": payload.folio_id, "reservation_id": payload.reservation_id})
        db.commit(); db.refresh(tx)
        return {"id": tx.id, "transaction_no": tx.transaction_no, "business_date": tx.business_date, "status": tx.status}
    except (ValueError, KeyError) as exc:
        db.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/folios/{folio_id}/windows", status_code=201)
def create_folio_window(folio_id: int, payload: WindowCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    if payload.guest_id and not db.get(Guest, payload.guest_id): raise HTTPException(status_code=400, detail="Guest does not exist")
    window = FolioWindow(folio_id=folio_id, name=payload.name, payer_type=payload.payer_type, guest_id=payload.guest_id, group_id=payload.group_id)
    db.add(window); db.flush(); audit(db, user.id, "create", "folio_window", window.id, {"folio_id": folio_id, "name": window.name}); db.commit(); db.refresh(window)
    return {"id": window.id, "folio_id": window.folio_id, "name": window.name, "payer_type": window.payer_type, "guest_id": window.guest_id, "group_id": window.group_id, "status": window.status}


@router.get("/folios/{folio_id}/windows")
def list_folio_windows(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    if not db.get(Folio, folio_id): raise HTTPException(status_code=404, detail="Folio not found")
    windows = db.scalars(select(FolioWindow).where(FolioWindow.folio_id == folio_id).order_by(FolioWindow.id)).all()
    return [{"id": w.id, "folio_id": w.folio_id, "name": w.name, "payer_type": w.payer_type, "guest_id": w.guest_id, "group_id": w.group_id, "status": w.status} for w in windows]


@router.post("/folios/{folio_id}/windows/{window_id}/transfer", status_code=201)
def transfer_item_to_window(folio_id: int, window_id: int, payload: WindowTransfer, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id); window = db.get(FolioWindow, window_id); item = db.get(FolioItem, payload.item_id)
    if not folio or not window or not item or window.folio_id != folio_id or item.folio_id != folio_id: raise HTTPException(status_code=404, detail="Folio, window or item not found")
    mapping = db.scalar(select(FolioItemWindow).where(FolioItemWindow.folio_item_id == item.id))
    if mapping: mapping.folio_window_id = window.id
    else: db.add(FolioItemWindow(folio_item_id=item.id, folio_window_id=window.id))
    audit(db, user.id, "route", "folio_item", item.id, {"folio_id": folio_id, "window_id": window.id}); db.commit()
    return {"item_id": item.id, "folio_id": folio_id, "window_id": window.id}


@router.get("/folios/{folio_id}/invoice")
def get_invoice(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    invoice = db.scalar(select(Invoice).where(Invoice.folio_id == folio_id))
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not issued")
    return {"id": invoice.id, "invoice_no": invoice.invoice_no, "folio_id": invoice.folio_id, "reservation_id": invoice.reservation_id, "business_date": invoice.business_date, "total": invoice.total, "currency": invoice.currency, "status": invoice.status, "issued_at": invoice.issued_at}


@router.post("/folios/{folio_id}/invoice", status_code=201)
def issue_invoice(folio_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio: raise HTTPException(status_code=404, detail="Folio not found")
    existing = db.scalar(select(Invoice).where(Invoice.folio_id == folio_id))
    if existing:
        return {"id": existing.id, "invoice_no": existing.invoice_no, "folio_id": existing.folio_id, "reservation_id": existing.reservation_id, "business_date": existing.business_date, "total": existing.total, "currency": existing.currency, "status": existing.status, "issued_at": existing.issued_at}
    if folio.status != "closed": raise HTTPException(status_code=409, detail="Invoice can only be issued for a closed folio")
    total, _, balance = folio_balance(db, folio)
    if balance != Decimal("0.00"): raise HTTPException(status_code=409, detail=f"Cannot issue invoice with outstanding balance of {balance}")
    state = db.get(BusinessDateState, 1); business_date = state.current_business_date if state else date.today()
    sequence = db.scalar(select(InvoiceSequence).where(InvoiceSequence.id == 1).with_for_update())
    if sequence is None: sequence = InvoiceSequence(id=1, last_number=0); db.add(sequence); db.flush()
    sequence.last_number += 1
    invoice = Invoice(invoice_no=f"INV-{business_date.year}-{sequence.last_number:06d}", folio_id=folio.id, reservation_id=folio.reservation_id, business_date=business_date, total=total, currency="PKR", status="issued", issued_by=user.id)
    db.add(invoice); db.flush(); audit(db, user.id, "issue", "invoice", invoice.id, {"invoice_no": invoice.invoice_no, "folio_id": folio_id, "total": str(total)}); db.commit(); db.refresh(invoice)
    return {"id": invoice.id, "invoice_no": invoice.invoice_no, "folio_id": invoice.folio_id, "reservation_id": invoice.reservation_id, "business_date": invoice.business_date, "total": invoice.total, "currency": invoice.currency, "status": invoice.status, "issued_at": invoice.issued_at}


@router.get("/ledger/reconciliation")
def ledger_reconciliation(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1); target_date = business_date or (state.current_business_date if state else date.today())
    transactions = db.scalars(select(FinancialTransaction).where(FinancialTransaction.business_date == target_date, FinancialTransaction.status == "posted")).all()
    transaction_ids = [tx.id for tx in transactions]
    entry_rows = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id.in_(transaction_ids))).all() if transaction_ids else []
    debits = money(sum((e.amount for e in entry_rows if e.direction == "debit"), Decimal("0.00")))
    credits = money(sum((e.amount for e in entry_rows if e.direction == "credit"), Decimal("0.00")))
    ledger_revenue = money(
        sum((e.amount for e in entry_rows if e.direction == "credit" and e.account.startswith("Revenue -")), Decimal("0.00"))
        - sum((e.amount for e in entry_rows if e.direction == "debit" and e.account.startswith("Revenue -")), Decimal("0.00"))
    )
    charge_receivable = money(
        sum((e.amount for e in entry_rows if e.account == "Guest Receivables" and e.direction == "debit" and next((tx.transaction_type for tx in transactions if tx.id == e.transaction_id), None) in {"folio_charge", "service_charge"}), Decimal("0.00"))
        - sum((e.amount for e in entry_rows if e.account == "Guest Receivables" and e.direction == "credit" and next((tx.transaction_type for tx in transactions if tx.id == e.transaction_id), None) == "folio_discount"), Decimal("0.00"))
    )
    payment_cash = money(sum((e.amount for e in entry_rows if e.account in CASH_ACCOUNTS and e.direction == "debit" and next((tx.transaction_type for tx in transactions if tx.id == e.transaction_id), None) == "folio_payment"), Decimal("0.00")))
    refund_cash = money(sum((e.amount for e in entry_rows if e.account in CASH_ACCOUNTS and e.direction == "credit" and next((tx.transaction_type for tx in transactions if tx.id == e.transaction_id), None) == "payment_refund"), Decimal("0.00")))
    settlement_receivable = money(
        sum((e.amount for e in entry_rows if e.account == "Guest Receivables" and e.direction == "credit" and next((tx.transaction_type for tx in transactions if tx.id == e.transaction_id), None) in {"folio_payment", "deposit_applied"}), Decimal("0.00"))
        - sum((e.amount for e in entry_rows if e.account == "Guest Receivables" and e.direction == "debit" and next((tx.transaction_type for tx in transactions if tx.id == e.transaction_id), None) == "payment_refund"), Decimal("0.00"))
    )
    net_cash = money(payment_cash - refund_cash)
    charge_difference = money(ledger_revenue - charge_receivable)
    settlement_difference = money(net_cash - settlement_receivable)
    status = "balanced" if debits == credits and charge_difference == Decimal("0.00") and settlement_difference == Decimal("0.00") else "review"
    return {
        "business_date": target_date,
        "ledger": {"transactions": len(transactions), "debits": debits, "credits": credits, "balanced": debits == credits, "revenue_credits": ledger_revenue, "cash_debits": payment_cash, "cash_credits": refund_cash, "net_cash": net_cash},
        "authority": {"folio_charges": charge_receivable, "payments": payment_cash, "refunds": refund_cash, "net_cash": net_cash},
        "reconciliation": {"charge_difference": charge_difference, "settlement_difference": settlement_difference, "status": status},
    }


@router.get("/night-audit/reconciliation")
def night_audit_reconciliation(db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1); target_date = state.current_business_date if state else date.today()
    return ledger_reconciliation(target_date, db, user)


@router.post("/night-audit/business-date/close")
def close_business_date(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    state = db.get(BusinessDateState, 1)
    if state is None:
        state = BusinessDateState(id=1, current_business_date=date.today(), opened_at=datetime.utcnow()); db.add(state); db.flush()
    report = ledger_reconciliation(state.current_business_date, db, user)
    if report["reconciliation"]["status"] != "balanced": raise HTTPException(status_code=409, detail={"message": "Ledger reconciliation requires review before business date can close", "reconciliation": report})
    closed_date = state.current_business_date; now = datetime.utcnow(); state.last_closed_at = now; state.current_business_date = closed_date + timedelta(days=1); state.opened_at = now
    audit(db, user.id, "business_date_close", "business_date", str(closed_date), {"closed_at": now.isoformat(), "next_business_date": str(state.current_business_date)})
    db.commit(); db.refresh(state)
    return {"closed_business_date": closed_date, "next_business_date": state.current_business_date, "closed_at": state.last_closed_at, "reconciliation": report}


@router.post("/stays/{stay_id}/deposits", status_code=201)
def create_deposit_with_ledger(
    stay_id: int,
    payload: dict,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "reception")),
):
    stay = db.get(Stay, stay_id)
    if not stay: raise HTTPException(status_code=404, detail="Stay not found")
    transaction_type = payload.get("transaction_type")
    amount = money(payload.get("amount", 0))
    if transaction_type not in {"received", "applied", "refunded", "adjusted"} or amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid deposit transaction")
    key = (idempotency_key or "").strip()
    if len(key) > 100:
        raise HTTPException(status_code=400, detail="Idempotency-Key must be 100 characters or fewer")
    if transaction_type in {"applied", "refunded"} and not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for deposit application/refund")

    if key:
        existing_tx = db.scalar(select(FinancialTransaction).where(FinancialTransaction.idempotency_key == key))
        if existing_tx is not None:
            if existing_tx.transaction_type != f"deposit_{transaction_type}" or existing_tx.reservation_id != stay.reservation_id:
                raise HTTPException(status_code=409, detail="Idempotency key is already bound to another financial operation")
            deposit_id = int(existing_tx.reference_id) if existing_tx.reference_id and existing_tx.reference_id.isdigit() else None
            existing_deposit = db.get(DepositTransaction, deposit_id) if deposit_id else None
            if existing_deposit is None or existing_deposit.stay_id != stay.id:
                raise HTTPException(status_code=409, detail="Idempotent deposit transaction is missing or incompatible")
            if money(existing_deposit.amount) != amount or existing_deposit.transaction_type != transaction_type:
                raise HTTPException(status_code=409, detail="Idempotency key is already bound to different deposit parameters")
            return {"id": existing_deposit.id, "stay_id": stay.id, "transaction_type": existing_deposit.transaction_type, "amount": existing_deposit.amount, "balance": stay_deposit_ledger_balance(db, stay.id), "replayed": True}

    folio = db.scalar(select(Folio).where(Folio.reservation_id == stay.reservation_id))
    reservation = db.get(Reservation, stay.reservation_id)
    current = stay_deposit_ledger_balance(db, stay.id)
    signed = amount if transaction_type in {"received", "adjusted"} else -amount
    new_balance = money(current + signed)
    if new_balance < 0:
        raise HTTPException(status_code=409, detail="Deposit transaction exceeds available deposit balance")
    if transaction_type == "received" and stay.deposit_required > 0 and new_balance > stay.deposit_required:
        raise HTTPException(status_code=409, detail="Deposit received exceeds required deposit")

    deposit = DepositTransaction(
        stay_id=stay.id,
        folio_id=folio.id if folio else None,
        transaction_type=transaction_type,
        amount=amount,
        payment_method=payload.get("payment_method"),
        reference=payload.get("reference") or (key or None),
        notes=payload.get("notes"),
        created_by=user.id,
    )
    db.add(deposit)
    db.flush()
    method = payload.get("payment_method") or "other"
    cash_account = {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(method, "Other Payment")
    financial_key = key or f"deposit:{deposit.id}"
    try:
        if transaction_type == "received":
            tx = post_deposit_received(db, stay_id=stay.id, folio_id=folio.id if folio else None, reservation_id=stay.reservation_id, deposit_id=deposit.id, amount=amount, method=payload.get("payment_method"), created_by=user.id)
        elif transaction_type == "refunded":
            tx = post_transaction(db, transaction_type="deposit_refunded", description=f"Deposit refund #{deposit.id}", reference_type="deposit", reference_id=str(deposit.id), folio_id=folio.id if folio else None, reservation_id=stay.reservation_id, created_by=user.id, idempotency_key=financial_key, lines=[{"account": "Guest Deposits", "direction": "debit", "amount": amount, "stay_id": stay.id}, {"account": cash_account, "direction": "credit", "amount": amount, "stay_id": stay.id, "payment_method": payload.get("payment_method")}])
        elif transaction_type == "applied":
            tx = post_transaction(db, transaction_type="deposit_applied", description=f"Deposit applied #{deposit.id}", reference_type="deposit", reference_id=str(deposit.id), folio_id=folio.id if folio else None, reservation_id=stay.reservation_id, created_by=user.id, idempotency_key=financial_key, lines=[{"account": "Guest Deposits", "direction": "debit", "amount": amount, "stay_id": stay.id}, {"account": "Guest Receivables", "direction": "credit", "amount": amount, "folio_id": folio.id if folio else None, "stay_id": stay.id}])
        else:
            tx = post_transaction(db, transaction_type="deposit_adjustment", description=f"Deposit adjustment #{deposit.id}", reference_type="deposit", reference_id=str(deposit.id), folio_id=folio.id if folio else None, reservation_id=stay.reservation_id, created_by=user.id, idempotency_key=financial_key, lines=[{"account": "Guest Deposits", "direction": "credit", "amount": amount, "stay_id": stay.id}, {"account": "Deposit Adjustments", "direction": "debit", "amount": amount, "stay_id": stay.id}])
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if tx.reference_id != str(deposit.id):
        db.delete(deposit)
        db.flush()
        replay_deposit_id = int(tx.reference_id) if tx.reference_id and tx.reference_id.isdigit() else None
        replay_deposit = db.get(DepositTransaction, replay_deposit_id) if replay_deposit_id else None
        if replay_deposit is None or replay_deposit.stay_id != stay.id:
            db.rollback()
            raise HTTPException(status_code=409, detail="Idempotent deposit transaction is missing or incompatible")
        return {"id": replay_deposit.id, "stay_id": stay.id, "transaction_type": replay_deposit.transaction_type, "amount": replay_deposit.amount, "balance": stay_deposit_ledger_balance(db, stay.id), "replayed": True}

    stay.deposit_received = stay_deposit_ledger_balance(db, stay.id)
    audit(db, user.id, "deposit", "stay", stay_id, {"deposit_transaction_id": deposit.id, "transaction_type": transaction_type, "amount": str(amount), "new_balance": str(stay.deposit_received)})
    db.commit()
    db.refresh(deposit)
    return {"id": deposit.id, "stay_id": stay.id, "transaction_type": deposit.transaction_type, "amount": deposit.amount, "balance": stay.deposit_received, "replayed": False}
