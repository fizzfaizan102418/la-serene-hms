from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_roles
from .db import get_db
from .financial_models import FolioItemWindow, Invoice, InvoiceSequence, PaymentRefund
from .ledger import post_deposit_received, post_transaction
from .models import BusinessDateState, DepositTransaction, Folio, FolioItem, Payment, Reservation, ReservationRoom, Room, User
from .pms_core import FolioWindow, Stay

router = APIRouter(prefix="", tags=["financial-operations"])
MONEY = Decimal("0.01")
FOOD_CATEGORIES = {"food", "restaurant", "room_service", "beverage", "drink", "snack"}


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
    items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio.id).order_by(FolioItem.id)).all()
    payments = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.folio_id == folio.id)) or 0
    refunds = db.scalar(select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(PaymentRefund.folio_id == folio.id)) or 0
    charges = sum((item_total(i) for i in items), Decimal("0.00")) + food_service_charge(items)
    paid_net = money(Decimal(payments) - Decimal(refunds))
    return money(charges), paid_net, money(max(Decimal("0.00"), charges - paid_net))


def audit(db: Session, user_id: int, action: str, entity_type: str, entity_id: int | str, details: dict) -> None:
    import json
    from .models import AuditLog
    db.add(AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=str(entity_id), details=json.dumps(details)))


@router.post("/reservations/{reservation_id}/check-out", response_model=CheckoutResponse)
def atomic_checkout(reservation_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    reservation = db.get(Reservation, reservation_id)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != "checked_in":
        raise HTTPException(status_code=409, detail="Reservation is not checked in")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == reservation_id))
    if not folio:
        raise HTTPException(status_code=409, detail="Reservation has no folio")
    total, paid, balance = folio_balance(db, folio)
    if balance != Decimal("0.00"):
        raise HTTPException(status_code=409, detail=f"Cannot check out with outstanding balance of {balance}")

    room_ids = db.scalars(select(ReservationRoom.room_id).where(ReservationRoom.reservation_id == reservation_id)).all()
    rooms = [db.get(Room, rid) for rid in room_ids]
    reservation.status = "checked_out"
    now = datetime.utcnow()
    for stay in db.scalars(select(Stay).where(Stay.reservation_id == reservation_id)).all():
        stay.status = "completed"
        stay.actual_check_out = stay.actual_check_out or now
    if folio.status == "open":
        folio.status = "closed"
    for room in rooms:
        if room and room.status == "occupied":
            room.status = "dirty"
    audit(db, user.id, "atomic_checkout", "reservation", reservation.id, {"folio_id": folio.id, "total": str(total), "paid": str(paid), "room_ids": room_ids})
    db.commit()
    return CheckoutResponse(id=reservation.id, status=reservation.status, folio_id=folio.id, room_ids=room_ids)


@router.post("/folios/{folio_id}/refunds", status_code=201)
def refund_payment(folio_id: int, payload: RefundCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    payment = db.get(Payment, payload.payment_id)
    if not folio or not payment or payment.folio_id != folio_id:
        raise HTTPException(status_code=404, detail="Folio or payment not found")
    already_refunded = db.scalar(select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(PaymentRefund.payment_id == payment.id)) or 0
    refundable = money(Decimal(payment.amount) - Decimal(already_refunded))
    if payload.amount > refundable:
        raise HTTPException(status_code=409, detail=f"Refund exceeds refundable payment balance of {refundable}")
    reservation = db.get(Reservation, folio.reservation_id)
    method = payload.method or payment.method
    refund = PaymentRefund(payment_id=payment.id, folio_id=folio_id, amount=money(payload.amount), method=method, reference=payload.reference, reason=payload.reason, created_by=user.id)
    db.add(refund); db.flush()
    cash_account = {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(method, "Other Payment")
    post_transaction(db, transaction_type="payment_refund", description=f"Refund payment #{payment.id}", reference_type="payment_refund", reference_id=str(refund.id), folio_id=folio_id, reservation_id=reservation.id if reservation else None, created_by=user.id, lines=[{"account": "Guest Receivables", "direction": "debit", "amount": refund.amount, "folio_id": folio_id}, {"account": cash_account, "direction": "credit", "amount": refund.amount, "folio_id": folio_id, "payment_method": method}])
    audit(db, user.id, "refund", "payment", payment.id, {"folio_id": folio_id, "refund_id": refund.id, "amount": str(refund.amount), "method": method, "reason": payload.reason})
    db.commit(); db.refresh(refund)
    _, paid_net, balance = folio_balance(db, folio)
    return {"id": refund.id, "payment_id": payment.id, "folio_id": folio_id, "amount": refund.amount, "method": method, "paid_net": paid_net, "balance": balance}


@router.post("/ledger/adjustments", status_code=201)
def create_adjustment(payload: AdjustmentCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    try:
        tx = post_transaction(db, transaction_type="adjustment", description=payload.description, reference_type="manual_adjustment", reference_id=payload.description[:50], folio_id=payload.folio_id, reservation_id=payload.reservation_id, created_by=user.id, lines=payload.lines)
        audit(db, user.id, "adjustment", "financial_transaction", tx.id, {"description": payload.description, "folio_id": payload.folio_id, "reservation_id": payload.reservation_id})
        db.commit(); db.refresh(tx)
        return {"id": tx.id, "transaction_no": tx.transaction_no, "business_date": tx.business_date, "status": tx.status}
    except (ValueError, KeyError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/folios/{folio_id}/windows", status_code=201)
def create_folio_window(folio_id: int, payload: WindowCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    if payload.guest_id and not db.get(__import__("app.models", fromlist=["Guest"]).Guest, payload.guest_id):
        raise HTTPException(status_code=400, detail="Guest does not exist")
    window = FolioWindow(folio_id=folio_id, name=payload.name, payer_type=payload.payer_type, guest_id=payload.guest_id, group_id=payload.group_id)
    db.add(window); db.flush()
    audit(db, user.id, "create", "folio_window", window.id, {"folio_id": folio_id, "name": window.name})
    db.commit(); db.refresh(window)
    return {"id": window.id, "folio_id": window.folio_id, "name": window.name, "payer_type": window.payer_type, "guest_id": window.guest_id, "group_id": window.group_id, "status": window.status}


@router.get("/folios/{folio_id}/windows")
def list_folio_windows(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception", "housekeeping"))):
    if not db.get(Folio, folio_id):
        raise HTTPException(status_code=404, detail="Folio not found")
    windows = db.scalars(select(FolioWindow).where(FolioWindow.folio_id == folio_id).order_by(FolioWindow.id)).all()
    return [{"id": w.id, "folio_id": w.folio_id, "name": w.name, "payer_type": w.payer_type, "guest_id": w.guest_id, "group_id": w.group_id, "status": w.status} for w in windows]


@router.post("/folios/{folio_id}/windows/{window_id}/transfer", status_code=201)
def transfer_item_to_window(folio_id: int, window_id: int, payload: WindowTransfer, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id); window = db.get(FolioWindow, window_id); item = db.get(FolioItem, payload.item_id)
    if not folio or not window or not item or window.folio_id != folio_id or item.folio_id != folio_id:
        raise HTTPException(status_code=404, detail="Folio, window or item not found")
    mapping = db.scalar(select(FolioItemWindow).where(FolioItemWindow.folio_item_id == item.id))
    if mapping:
        mapping.folio_window_id = window.id
    else:
        db.add(FolioItemWindow(folio_item_id=item.id, folio_window_id=window.id))
    audit(db, user.id, "route", "folio_item", item.id, {"folio_id": folio_id, "window_id": window.id})
    db.commit()
    return {"item_id": item.id, "folio_id": folio_id, "window_id": window.id}


@router.get("/folios/{folio_id}/invoice")
def get_invoice(folio_id: int, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    invoice = db.scalar(select(Invoice).where(Invoice.folio_id == folio_id))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not issued")
    return {"id": invoice.id, "invoice_no": invoice.invoice_no, "folio_id": invoice.folio_id, "reservation_id": invoice.reservation_id, "business_date": invoice.business_date, "total": invoice.total, "currency": invoice.currency, "status": invoice.status, "issued_at": invoice.issued_at}


@router.post("/folios/{folio_id}/invoice", status_code=201)
def issue_invoice(folio_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    folio = db.get(Folio, folio_id)
    if not folio:
        raise HTTPException(status_code=404, detail="Folio not found")
    existing = db.scalar(select(Invoice).where(Invoice.folio_id == folio_id))
    if existing:
        return {"id": existing.id, "invoice_no": existing.invoice_no, "folio_id": existing.folio_id, "reservation_id": existing.reservation_id, "business_date": existing.business_date, "total": existing.total, "currency": existing.currency, "status": existing.status, "issued_at": existing.issued_at}
    if folio.status != "closed":
        raise HTTPException(status_code=409, detail="Invoice can only be issued for a closed folio")
    total, _, balance = folio_balance(db, folio)
    if balance != Decimal("0.00"):
        raise HTTPException(status_code=409, detail=f"Cannot issue invoice with outstanding balance of {balance}")
    state = db.get(BusinessDateState, 1)
    business_date = state.current_business_date if state else date.today()
    sequence = db.scalar(select(InvoiceSequence).where(InvoiceSequence.id == 1).with_for_update())
    if sequence is None:
        sequence = InvoiceSequence(id=1, last_number=0); db.add(sequence); db.flush()
    sequence.last_number += 1
    invoice = Invoice(invoice_no=f"INV-{business_date.year}-{sequence.last_number:06d}", folio_id=folio.id, reservation_id=folio.reservation_id, business_date=business_date, total=total, currency="PKR", status="issued", issued_by=user.id)
    db.add(invoice); db.flush()
    audit(db, user.id, "issue", "invoice", invoice.id, {"invoice_no": invoice.invoice_no, "folio_id": folio_id, "total": str(total)})
    db.commit(); db.refresh(invoice)
    return {"id": invoice.id, "invoice_no": invoice.invoice_no, "folio_id": invoice.folio_id, "reservation_id": invoice.reservation_id, "business_date": invoice.business_date, "total": invoice.total, "currency": invoice.currency, "status": invoice.status, "issued_at": invoice.issued_at}


@router.get("/ledger/reconciliation")
def ledger_reconciliation(business_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    target_date = business_date or (state.current_business_date if state else date.today())
    from .models import FinancialTransaction, LedgerEntry
    transactions = db.scalars(select(FinancialTransaction).where(FinancialTransaction.business_date == target_date, FinancialTransaction.status.in_(("posted", "reversed")))).all()
    transaction_ids = [tx.id for tx in transactions]
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id.in_(transaction_ids))) if transaction_ids else []
    entry_rows = entries.all() if transaction_ids else []
    debits = money(sum((e.amount for e in entry_rows if e.direction == "debit"), Decimal("0.00")))
    credits = money(sum((e.amount for e in entry_rows if e.direction == "credit"), Decimal("0.00")))
    daily_items = db.scalars(select(FolioItem).where(FolioItem.created_at >= datetime.combine(target_date, datetime.min.time()), FolioItem.created_at < datetime.combine(target_date, datetime.min.time()) + __import__("datetime").timedelta(days=1))).all()
    operational_charges = money(sum((item_total(i) for i in daily_items), Decimal("0.00")))
    ledger_revenue = money(sum((e.amount for e in entry_rows if e.direction == "credit" and e.account.startswith("Revenue -")), Decimal("0.00")))
    daily_payments = money(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.created_at >= datetime.combine(target_date, datetime.min.time()), Payment.created_at < datetime.combine(target_date, datetime.min.time()) + __import__("datetime").timedelta(days=1))) or 0)
    ledger_cash_collections = money(sum((e.amount for e in entry_rows if e.direction == "debit" and e.account in {"Cash", "Card Clearing", "Bank", "Other Payment"}), Decimal("0.00")))
    daily_refunds = money(db.scalar(select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(PaymentRefund.created_at >= datetime.combine(target_date, datetime.min.time()), PaymentRefund.created_at < datetime.combine(target_date, datetime.min.time()) + __import__("datetime").timedelta(days=1))) or 0)
    return {"business_date": target_date, "ledger": {"transactions": len(transactions), "debits": debits, "credits": credits, "balanced": debits == credits, "revenue_credits": ledger_revenue, "cash_collections": ledger_cash_collections}, "operational": {"folio_charges": operational_charges, "payments": daily_payments, "refunds": daily_refunds}, "reconciliation": {"charge_difference": money(ledger_revenue - operational_charges), "payment_difference": money(ledger_cash_collections - daily_payments), "status": "balanced" if debits == credits and money(ledger_cash_collections - daily_payments - daily_refunds) == Decimal("0.00") and money(ledger_revenue - operational_charges) == Decimal("0.00") else "review"}}


@router.get("/night-audit/reconciliation")
def night_audit_reconciliation(db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "reception"))):
    state = db.get(BusinessDateState, 1)
    target_date = state.current_business_date if state else date.today()
    return ledger_reconciliation(target_date, db, _)


@router.post("/night-audit/business-date/close")
def close_business_date(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    state = db.get(BusinessDateState, 1)
    if state is None:
        state = BusinessDateState(id=1, current_business_date=date.today(), opened_at=datetime.utcnow())
        db.add(state); db.flush()
    report = ledger_reconciliation(state.current_business_date, db, user)
    if report["reconciliation"]["status"] != "balanced":
        raise HTTPException(status_code=409, detail={"message": "Ledger reconciliation requires review before business date can close", "reconciliation": report})
    closed_date = state.current_business_date
    now = datetime.utcnow()
    state.last_closed_at = now
    state.current_business_date = closed_date.fromordinal(closed_date.toordinal() + 1)
    state.opened_at = now
    audit(db, user.id, "business_date_close", "business_date", str(closed_date), {"closed_at": now.isoformat(), "next_business_date": str(state.current_business_date)})
    db.commit(); db.refresh(state)
    return {"closed_business_date": closed_date, "next_business_date": state.current_business_date, "closed_at": state.last_closed_at, "reconciliation": report}


@router.post("/stays/{stay_id}/deposits", status_code=201)
def create_deposit_with_ledger(stay_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "reception"))):
    stay = db.get(Stay, stay_id)
    if not stay:
        raise HTTPException(status_code=404, detail="Stay not found")
    transaction_type = payload.get("transaction_type")
    amount = money(payload.get("amount", 0))
    if transaction_type not in {"received", "applied", "refunded", "adjusted"} or amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid deposit transaction")
    current = Decimal("0.00")
    for item in db.scalars(select(DepositTransaction).where(DepositTransaction.stay_id == stay_id)).all():
        current += item.amount if item.transaction_type in {"received", "adjusted"} else -item.amount
    signed = amount if transaction_type in {"received", "adjusted"} else -amount
    new_balance = money(current + signed)
    if new_balance < 0:
        raise HTTPException(status_code=409, detail="Deposit transaction exceeds available deposit balance")
    if transaction_type == "received" and stay.deposit_required > 0 and new_balance > stay.deposit_required:
        raise HTTPException(status_code=409, detail="Deposit received exceeds required deposit")
    folio = db.scalar(select(Folio).where(Folio.reservation_id == stay.reservation_id))
    tx = DepositTransaction(stay_id=stay.id, folio_id=folio.id if folio else None, transaction_type=transaction_type, amount=amount, payment_method=payload.get("payment_method"), reference=payload.get("reference"), notes=payload.get("notes"), created_by=user.id)
    db.add(tx); db.flush()
    cash_account = {"cash": "Cash", "card": "Card Clearing", "bank_transfer": "Bank", "other": "Other Payment"}.get(payload.get("payment_method") or "other", "Other Payment")
    reservation_id = stay.reservation_id
    if transaction_type == "received":
        post_deposit_received(db, stay_id=stay.id, folio_id=folio.id if folio else None, reservation_id=reservation_id, deposit_id=tx.id, amount=amount, method=payload.get("payment_method"), created_by=user.id)
    elif transaction_type == "refunded":
        post_transaction(db, transaction_type="deposit_refund", description=f"Deposit refund #{tx.id}", reference_type="deposit", reference_id=str(tx.id), folio_id=folio.id if folio else None, reservation_id=reservation_id, created_by=user.id, lines=[{"account": "Guest Deposits", "direction": "debit", "amount": amount, "stay_id": stay.id}, {"account": cash_account, "direction": "credit", "amount": amount, "stay_id": stay.id, "payment_method": payload.get("payment_method")}])
    elif transaction_type == "applied":
        post_transaction(db, transaction_type="deposit_applied", description=f"Deposit applied #{tx.id}", reference_type="deposit", reference_id=str(tx.id), folio_id=folio.id if folio else None, reservation_id=reservation_id, created_by=user.id, lines=[{"account": "Guest Deposits", "direction": "debit", "amount": amount, "stay_id": stay.id}, {"account": "Guest Receivables", "direction": "credit", "amount": amount, "folio_id": folio.id if folio else None, "stay_id": stay.id}])
    else:
        post_transaction(db, transaction_type="deposit_adjustment", description=f"Deposit adjustment #{tx.id}", reference_type="deposit", reference_id=str(tx.id), folio_id=folio.id if folio else None, reservation_id=reservation_id, created_by=user.id, lines=[{"account": "Guest Deposits", "direction": "credit", "amount": amount, "stay_id": stay.id}, {"account": "Deposit Adjustments", "direction": "debit", "amount": amount, "stay_id": stay.id}])
    stay.deposit_received = new_balance
    audit(db, user.id, "deposit", "stay", stay_id, {"deposit_transaction_id": tx.id, "transaction_type": transaction_type, "amount": str(amount), "new_balance": str(new_balance)})
    db.commit(); db.refresh(tx)
    return {"id": tx.id, "stay_id": stay.id, "transaction_type": tx.transaction_type, "amount": tx.amount, "balance": new_balance}
