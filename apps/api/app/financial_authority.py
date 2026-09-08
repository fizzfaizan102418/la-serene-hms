from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import event, func, or_, select
from sqlalchemy.orm import Session

from .models import DepositTransaction, FinancialTransaction, Folio, FolioItem, LedgerEntry, Payment
from .financial_models import Invoice, PaymentRefund

MONEY = Decimal("0.01")
FOOD_CATEGORIES = {"food", "restaurant", "room_service", "beverage", "drink", "snack"}


@dataclass(frozen=True)
class FolioLedgerSummary:
    total: Decimal
    paid: Decimal
    balance: Decimal


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def folio_ledger_summary(db: Session, folio_id: int) -> FolioLedgerSummary:
    """Return the folio financial position from posted Guest Receivables ledger entries."""
    debit_total = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted")) or Decimal("0.00")
    credit_total = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted")) or Decimal("0.00")
    settlement_credits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted", FinancialTransaction.transaction_type.in_(("folio_payment", "deposit_applied")))) or Decimal("0.00")
    refund_debits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted", FinancialTransaction.transaction_type == "payment_refund")) or Decimal("0.00")
    raw_balance = Decimal(debit_total) - Decimal(credit_total)
    paid = money(Decimal(settlement_credits) - Decimal(refund_debits))
    total = money(max(Decimal("0.00"), raw_balance + paid))
    balance = money(max(Decimal("0.00"), raw_balance))
    return FolioLedgerSummary(total=total, paid=paid, balance=balance)


def post_folio_charge_authoritative(db: Session, *, folio_id: int, reservation_id: int, item_id: int, amount: Decimal, stay_id: int | None, category: str, created_by: int, gross_amount: Decimal | None = None, discount_amount: Decimal | None = None) -> FinancialTransaction:
    """Post gross charge, explicit discount, and food service charge to the ledger."""
    from .ledger import post_transaction

    net_amount = money(amount)
    gross = money(gross_amount if gross_amount is not None else net_amount)
    discount = money(discount_amount if discount_amount is not None else max(Decimal("0.00"), gross - net_amount))
    if money(gross - discount) != net_amount:
        raise ValueError("Gross charge minus discount must equal the net charge")
    transaction = post_transaction(db, transaction_type="folio_charge", description=f"Folio charge #{item_id}: {category}", reference_type="folio_item", reference_id=str(item_id), folio_id=folio_id, reservation_id=reservation_id, created_by=created_by, idempotency_key=f"folio-charge:{item_id}", lines=[{"account": "Guest Receivables", "direction": "debit", "amount": gross, "folio_id": folio_id, "stay_id": stay_id}, {"account": "Revenue - " + (category[:45] or "Other"), "direction": "credit", "amount": gross, "folio_id": folio_id, "stay_id": stay_id}])
    if discount > 0:
        post_transaction(db, transaction_type="folio_discount", description=f"Discount for folio item #{item_id}", reference_type="folio_item_discount", reference_id=str(item_id), folio_id=folio_id, reservation_id=reservation_id, created_by=created_by, idempotency_key=f"folio-discount:{item_id}", lines=[{"account": "Revenue - " + (category[:45] or "Other"), "direction": "debit", "amount": discount, "folio_id": folio_id, "stay_id": stay_id}, {"account": "Guest Receivables", "direction": "credit", "amount": discount, "folio_id": folio_id, "stay_id": stay_id}])
    if category.strip().lower() in FOOD_CATEGORIES:
        service_charge = money(net_amount * Decimal("0.10"))
        if service_charge > 0:
            post_transaction(db, transaction_type="service_charge", description=f"Food service charge for folio item #{item_id}", reference_type="folio_item_service_charge", reference_id=str(item_id), folio_id=folio_id, reservation_id=reservation_id, created_by=created_by, idempotency_key=f"folio-service-charge:{item_id}", lines=[{"account": "Guest Receivables", "direction": "debit", "amount": service_charge, "folio_id": folio_id, "stay_id": stay_id}, {"account": "Revenue - service_charge", "direction": "credit", "amount": service_charge, "folio_id": folio_id, "stay_id": stay_id}])
    return transaction


def has_posted_folio_item_transaction(db: Session, folio_item_id: int) -> bool:
    return db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.reference_type == "folio_item", FinancialTransaction.reference_id == str(folio_item_id), FinancialTransaction.status.in_(("posted", "reversed"))).limit(1)) is not None


def _has_transaction(connection, reference_type: str, reference_id: int) -> bool:
    return connection.execute(select(FinancialTransaction.id).where(FinancialTransaction.reference_type == reference_type, FinancialTransaction.reference_id == str(reference_id)).limit(1)).scalar_one_or_none() is not None


def _has_deposit_transaction(connection, target: DepositTransaction) -> bool:
    return connection.execute(select(FinancialTransaction.id).where(or_(((FinancialTransaction.reference_type == "deposit") & (FinancialTransaction.reference_id == str(target.id))), ((FinancialTransaction.reference_type == "deposit_transfer") & (FinancialTransaction.reference_id == target.reference)))).limit(1)).scalar_one_or_none() is not None


def _posted_receivable_balance(connection, folio_id: int) -> tuple[Decimal, bool]:
    has_history = connection.execute(select(FinancialTransaction.id).join(LedgerEntry, LedgerEntry.transaction_id == FinancialTransaction.id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", FinancialTransaction.status == "posted").limit(1)).scalar_one_or_none() is not None
    debits = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted")).scalar_one() or Decimal("0.00")
    credits = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted")).scalar_one() or Decimal("0.00")
    return money(max(Decimal("0.00"), Decimal(debits) - Decimal(credits))), has_history


def _posted_deposit_balance(connection, stay_id: int) -> tuple[Decimal, bool]:
    has_history = connection.execute(select(FinancialTransaction.id).join(LedgerEntry, LedgerEntry.transaction_id == FinancialTransaction.id).where(LedgerEntry.stay_id == stay_id, LedgerEntry.account == "Guest Deposits", FinancialTransaction.status == "posted").limit(1)).scalar_one_or_none() is not None
    credits = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == stay_id, LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted")).scalar_one() or Decimal("0.00")
    debits = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.stay_id == stay_id, LedgerEntry.account == "Guest Deposits", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted")).scalar_one() or Decimal("0.00")
    return money(max(Decimal("0.00"), Decimal(credits) - Decimal(debits))), has_history


def _posted_payment_refunds(connection, payment_id: int) -> Decimal:
    refund_ids = connection.execute(select(PaymentRefund.id).where(PaymentRefund.payment_id == payment_id)).scalars().all()
    if not refund_ids:
        return Decimal("0.00")
    value = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(FinancialTransaction.transaction_type == "payment_refund", FinancialTransaction.status == "posted", FinancialTransaction.reference_type == "payment_refund", FinancialTransaction.reference_id.in_([str(refund_id) for refund_id in refund_ids]), LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "debit")).scalar_one() or Decimal("0.00")
    return money(value)


def _guard_concurrent_posting(connection, target: FinancialTransaction) -> None:
    if target.transaction_type == "folio_payment" and target.folio_id is not None:
        connection.execute(select(Folio.id).where(Folio.id == target.folio_id).with_for_update()).scalar_one_or_none()
        payment_id = int(target.reference_id) if target.reference_type == "payment" and target.reference_id and target.reference_id.isdigit() else None
        if payment_id is not None:
            amount = connection.execute(select(Payment.amount).where(Payment.id == payment_id)).scalar_one_or_none()
            if amount is not None:
                remaining, has_history = _posted_receivable_balance(connection, target.folio_id)
                if has_history and money(amount) > remaining:
                    raise HTTPException(status_code=409, detail="Payment exceeds the remaining authoritative folio balance")

    if target.transaction_type == "payment_refund" and target.reference_type == "payment_refund" and target.reference_id:
        refund_id = int(target.reference_id) if target.reference_id.isdigit() else None
        if refund_id is not None:
            payment_id = connection.execute(select(PaymentRefund.payment_id).where(PaymentRefund.id == refund_id)).scalar_one_or_none()
            if payment_id is not None:
                has_payment_history = connection.execute(select(FinancialTransaction.id).join(LedgerEntry, LedgerEntry.transaction_id == FinancialTransaction.id).where(FinancialTransaction.transaction_type == "folio_payment", FinancialTransaction.status == "posted", LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "credit", FinancialTransaction.folio_id == target.folio_id).limit(1)).scalar_one_or_none() is not None
                if has_payment_history:
                    connection.execute(select(Payment.id).where(Payment.id == payment_id).with_for_update()).scalar_one_or_none()
                    payment_amount = connection.execute(select(Payment.amount).where(Payment.id == payment_id)).scalar_one_or_none()
                    if payment_amount is not None:
                        refundable = money(Decimal(payment_amount) - _posted_payment_refunds(connection, payment_id))
                        refund_amount = connection.execute(select(PaymentRefund.amount).where(PaymentRefund.id == refund_id)).scalar_one_or_none() or Decimal("0.00")
                        if money(refund_amount) > refundable:
                            raise HTTPException(status_code=409, detail=f"Refund exceeds refundable payment balance of {refundable}")

    if target.transaction_type in {"deposit_applied", "deposit_refund"} and target.reference_type == "deposit" and target.reference_id:
        deposit_id = int(target.reference_id) if target.reference_id.isdigit() else None
        if deposit_id is not None:
            stay_id = connection.execute(select(DepositTransaction.stay_id).where(DepositTransaction.id == deposit_id)).scalar_one_or_none()
            if stay_id is not None:
                from .pms_core import Stay
                remaining, has_history = _posted_deposit_balance(connection, stay_id)
                if has_history:
                    connection.execute(select(Stay.id).where(Stay.id == stay_id).with_for_update()).scalar_one_or_none()
                    deposit_amount = connection.execute(select(DepositTransaction.amount).where(DepositTransaction.id == deposit_id)).scalar_one_or_none() or Decimal("0.00")
                    if money(deposit_amount) > remaining:
                        raise HTTPException(status_code=409, detail="Deposit transaction exceeds the remaining authoritative deposit balance")


@event.listens_for(FinancialTransaction, "before_insert")
def guard_concurrent_financial_posting(mapper, connection, target: FinancialTransaction) -> None:
    _guard_concurrent_posting(connection, target)


@event.listens_for(FolioItem, "before_update")
def prevent_posted_folio_item_update(mapper, connection, target: FolioItem) -> None:
    if _has_transaction(connection, "folio_item", target.id):
        raise ValueError("Posted folio charges are immutable; use a ledger adjustment or reversal")


@event.listens_for(FolioItem, "before_delete")
def prevent_posted_folio_item_delete(mapper, connection, target: FolioItem) -> None:
    if _has_transaction(connection, "folio_item", target.id):
        raise ValueError("Posted folio charges are immutable; use a ledger adjustment or reversal")


@event.listens_for(Payment, "before_update")
@event.listens_for(Payment, "before_delete")
def prevent_payment_mutation(mapper, connection, target: Payment) -> None:
    if _has_transaction(connection, "payment", target.id):
        raise ValueError("Posted payments are immutable; post a refund instead")


@event.listens_for(PaymentRefund, "before_update")
@event.listens_for(PaymentRefund, "before_delete")
def prevent_refund_mutation(mapper, connection, target: PaymentRefund) -> None:
    if _has_transaction(connection, "payment_refund", target.id):
        raise ValueError("Posted refunds are immutable")


@event.listens_for(DepositTransaction, "before_update")
@event.listens_for(DepositTransaction, "before_delete")
def prevent_deposit_mutation(mapper, connection, target: DepositTransaction) -> None:
    if _has_deposit_transaction(connection, target):
        raise ValueError("Posted deposit transactions are immutable; use a new deposit transaction")


@event.listens_for(Invoice, "before_insert")
def derive_invoice_total_from_ledger(mapper, connection, target: Invoice) -> None:
    if target.folio_id is None:
        return
    debit_total = _receivable_sum(connection, folio_id=target.folio_id, direction="debit")
    credit_total = _receivable_sum(connection, folio_id=target.folio_id, direction="credit")
    settlement_credits = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == target.folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted", FinancialTransaction.transaction_type.in_(("folio_payment", "deposit_applied")))).scalar_one() or Decimal("0.00")
    refund_debits = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == target.folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted", FinancialTransaction.transaction_type == "payment_refund")).scalar_one() or Decimal("0.00")
    target.total = money(max(Decimal("0.00"), Decimal(debit_total) - Decimal(credit_total) + Decimal(settlement_credits) - Decimal(refund_debits)))


def _receivable_sum(connection, *, folio_id: int, direction: str) -> Decimal:
    value = connection.execute(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == direction, FinancialTransaction.status == "posted")).scalar_one()
    return Decimal(value or 0)
