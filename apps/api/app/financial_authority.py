from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FinancialTransaction, LedgerEntry

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class FolioLedgerSummary:
    total: Decimal
    paid: Decimal
    balance: Decimal


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def folio_ledger_summary(db: Session, folio_id: int) -> FolioLedgerSummary:
    """Return the folio balance from posted ledger entries, not operational payment/item tables.

    Guest Receivables debits increase the amount owed; credits reduce it. Payment credits
    and payment-refund debits are used to derive the net settled amount. This keeps refunds
    from being mistaken for revenue while preserving the receivable balance.
    """
    debit_total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == folio_id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "debit",
            FinancialTransaction.status == "posted",
        )
    ) or Decimal("0.00")
    credit_total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == folio_id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "credit",
            FinancialTransaction.status == "posted",
        )
    ) or Decimal("0.00")

    settlement_credits = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == folio_id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "credit",
            FinancialTransaction.status == "posted",
            FinancialTransaction.transaction_type.in_(("folio_payment", "deposit_applied")),
        )
    ) or Decimal("0.00")
    refund_debits = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == folio_id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "debit",
            FinancialTransaction.status == "posted",
            FinancialTransaction.transaction_type == "payment_refund",
        )
    ) or Decimal("0.00")

    paid = money(Decimal(settlement_credits) - Decimal(refund_debits))
    total = money(Decimal(debit_total) - Decimal(refund_debits))
    # Corrections/adjustments that touch Guest Receivables are naturally reflected by
    # the net ledger position. The derived balance is never allowed to go negative.
    balance = money(max(Decimal("0.00"), Decimal(debit_total) - Decimal(credit_total)))
    total = money(balance + paid)
    return FolioLedgerSummary(total=total, paid=paid, balance=balance)


def has_posted_folio_item_transaction(db: Session, folio_item_id: int) -> bool:
    return db.scalar(
        select(FinancialTransaction.id)
        .where(
            FinancialTransaction.reference_type == "folio_item",
            FinancialTransaction.reference_id == str(folio_item_id),
            FinancialTransaction.status.in_(("posted", "reversed")),
        )
        .limit(1)
    ) is not None
