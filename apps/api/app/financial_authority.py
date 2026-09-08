from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from .models import DepositTransaction, FinancialTransaction, FolioItem, Invoice, LedgerEntry, Payment
from .financial_models import PaymentRefund

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
    debit_total = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted"))) or Decimal("0.00")
    credit_total = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted"))) or Decimal("0.00")
    settlement_credits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "credit", FinancialTransaction.status == "posted", FinancialTransaction.transaction_type.in_(("folio_payment", "deposit_applied"))))) or Decimal("0.00")
    refund_debits = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount), 0)).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(LedgerEntry.folio_id == folio_id, LedgerEntry.account == "Guest Receivables", LedgerEntry.direction == "debit", FinancialTransaction.status == "posted", FinancialTransaction.transaction_type == "payment_refund"))) or Decimal("0.00")
    raw_balance = Decimal(debit_total) - Decimal(credit_total)
    paid = money(Decimal(settlement_credits) - Decimal(refund_debits))
    total = money(max(Decimal("0.00"), raw_balance + paid))
    balance = money(max(Decimal("0.00"), raw_balance))
    return FolioLedgerSummary(total=total, paid=paid, balance=balance)


def post_folio_charge_authoritative(
    db: Session,
    *,
    folio_id: int,
    reservation_id: int,
    item_id: int,
    amount: Decimal,
    stay_id: int | None,
    category: str,
    created_by: int,
    gross_amount: Decimal | None = None,
    discount_amount: Decimal | None = None,
) -> FinancialTransaction:
    """Post gross charge, explicit discount, and food service charge to the ledger."""
    from .ledger import post_transaction

    net_amount = money(amount)
    gross = money(gross_amount if gross_amount is not None else net_amount)
    discount = money(discount_amount if discount_amount is not None else max(Decimal("0.00"), gross - net_amount))
    if money(gross - discount) != net_amount:
        raise ValueError("Gross charge minus discount must equal the net charge")
    transaction = post_transaction(db, transaction_type="folio_charge", description=f"Folio charge #{item_id}: {category}", reference_type="folio_item", reference_id=str(item_id), folio_id=folio_id, reservation_id=reservation_id, created_by=created_by, idempotency_key=f"folio-charge:{item_id}", lines=[
        {"account": "Guest Receivables", "direction": "debit", "amount": gross, "folio_id": folio_id, "stay_id": stay_id},
        {"account": "Revenue - " + (category[:45] or "Other"), "direction": "credit", "amount": gross, "folio_id": folio_id, "stay_id": stay_id},
    ])
    if discount > 0:
        post_transaction(db, transaction_type="folio_discount", description=f"Discount for folio item #{item_id}", reference_type="folio_item_discount", reference_id=str(item_id), folio_id=folio_id, reservation_id=reservation_id, created_by=created_by, idempotency_key=f"folio-discount:{item_id}", lines=[
            {"account": "Revenue - " + (category[:45] or "Other"), "direction": "debit", "amount": discount, "folio_id": folio_id, "stay_id": stay_id},
            {"account": "Guest Receivables", "direction": "credit", "amount": discount, "folio_id": folio_id, "stay_id": stay_id},
        ])
    if category.strip().lower() in FOOD_CATEGORIES:
        service_charge = money(net_amount * Decimal("0.10"))
        if service_charge > 0:
            post_transaction(db, transaction_type="service_charge", description=f"Food service charge for folio item #{item_id}", reference_type="folio_item_service_charge", reference_id=str(item_id), folio_id=folio_id, reservation_id=reservation_id, created_by=created_by, idempotency_key=f"folio-service-charge:{item_id}", lines=[
                {"account": "Guest Receivables", "direction": "debit", "amount": service_charge, "folio_id": folio_id, "stay_id": stay_id},
                {"account": "Revenue - service_charge", "direction": "credit", "amount": service_charge, "folio_id": folio_id, "stay_id": stay_id},
            ])
    return transaction


def has_posted_folio_item_transaction(db: Session, folio_item_id: int) -> bool:
    return db.scalar(select(FinancialTransaction.id).where(FinancialTransaction.reference_type == "folio_item", FinancialTransaction.reference_id == str(folio_item_id), FinancialTransaction.status.in_(("posted", "reversed"))).limit(1)) is not None


def _has_transaction(connection, reference_type: str, reference_id: int) -> bool:
    return connection.execute(select(FinancialTransaction.id).where(FinancialTransaction.reference_type == reference_type, FinancialTransaction.reference_id == str(reference_id)).limit(1)).scalar_one_or_none() is not None


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
    if _has_transaction(connection, "deposit", target.id):
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
