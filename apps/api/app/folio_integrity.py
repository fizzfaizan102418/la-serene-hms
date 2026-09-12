from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from .models import FinancialTransaction, Folio, FolioItem, LedgerEntry

MONEY = Decimal("0.01")
FOOD_CATEGORIES = {"food", "restaurant", "room_service", "beverage", "drink", "snack"}


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def item_line_total(item: FolioItem) -> Decimal:
    gross = Decimal(item.quantity) * Decimal(item.unit_price)
    return money(max(Decimal("0.00"), gross - Decimal(item.discount)))


def item_has_active_charge(db: Session, item_id: int) -> bool:
    transactions = db.scalars(
        select(FinancialTransaction)
        .where(
            FinancialTransaction.reference_id == str(item_id),
            FinancialTransaction.reference_type == "folio_item",
        )
        .order_by(FinancialTransaction.id)
    ).all()
    for transaction in transactions:
        if transaction.status != "posted":
            continue
        reversed_exists = db.scalar(
            select(FinancialTransaction.id)
            .where(FinancialTransaction.reversal_of_id == transaction.id)
            .limit(1)
        )
        if reversed_exists is None:
            return True
    return False


def expected_active_total(db: Session, folio_id: int) -> Decimal:
    total = Decimal("0.00")
    food_net = Decimal("0.00")
    items = db.scalars(select(FolioItem).where(FolioItem.folio_id == folio_id).order_by(FolioItem.id)).all()
    for item in items:
        if not item_has_active_charge(db, item.id):
            continue
        line_total = item_line_total(item)
        total += line_total
        if item.category.strip().lower() in FOOD_CATEGORIES:
            food_net += line_total
    total += money(food_net * Decimal("0.10"))
    return money(total)


def ledger_total(db: Session, folio_id: int) -> Decimal:
    from .financial_authority import folio_ledger_summary
    return money(folio_ledger_summary(db, folio_id).total)


def integrity_snapshot(db: Session, folio_id: int) -> dict:
    expected = expected_active_total(db, folio_id)
    actual = ledger_total(db, folio_id)
    return {
        "folio_id": folio_id,
        "expected_active_total": expected,
        "ledger_total": actual,
        "difference": money(expected - actual),
        "ok": expected == actual,
    }


def ensure_folio_close_integrity(db: Session, folio_id: int) -> None:
    snapshot = integrity_snapshot(db, folio_id)
    if not snapshot["ok"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "Folio financial integrity mismatch; checkout/closure is blocked. "
                f"Expected active charges {snapshot['expected_active_total']}, "
                f"authoritative ledger total {snapshot['ledger_total']}. "
                "Reconcile the folio before closing."
            ),
        )


@event.listens_for(Folio, "before_update")
def guard_folio_close(mapper, connection, target: Folio) -> None:
    if target.status != "closed":
        return
    history = inspect(target).attrs.status.history
    if not history.has_changes():
        return
    if history.deleted and history.deleted[0] == "closed":
        return
    snapshot_expected = Decimal("0.00")
    items = connection.execute(
        select(FolioItem.id, FolioItem.quantity, FolioItem.unit_price, FolioItem.discount, FolioItem.category)
        .where(FolioItem.folio_id == target.id)
        .order_by(FolioItem.id)
    ).all()
    food_net = Decimal("0.00")
    for item_id, quantity, unit_price, discount, category in items:
        transactions = connection.execute(
            select(FinancialTransaction.id, FinancialTransaction.status)
            .where(
                FinancialTransaction.reference_id == str(item_id),
                FinancialTransaction.reference_type == "folio_item",
                FinancialTransaction.status == "posted",
            )
        ).all()
        active = False
        for transaction_id, _status in transactions:
            reversal = connection.execute(
                select(FinancialTransaction.id)
                .where(FinancialTransaction.reversal_of_id == transaction_id)
                .limit(1)
            ).scalar_one_or_none()
            if reversal is None:
                active = True
                break
        if not active:
            continue
        line_total = money(max(Decimal("0.00"), Decimal(quantity) * Decimal(unit_price) - Decimal(discount)))
        snapshot_expected += line_total
        if str(category).strip().lower() in FOOD_CATEGORIES:
            food_net += line_total
    snapshot_expected += money(food_net * Decimal("0.10"))

    from sqlalchemy import func
    debit_total = connection.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == target.id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "debit",
            FinancialTransaction.status == "posted",
        )
    ).scalar_one() or Decimal("0.00")
    credit_total = connection.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == target.id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "credit",
            FinancialTransaction.status == "posted",
        )
    ).scalar_one() or Decimal("0.00")
    settlement_credits = connection.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == target.id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "credit",
            FinancialTransaction.status == "posted",
            FinancialTransaction.transaction_type.in_(("folio_payment", "deposit_applied")),
        )
    ).scalar_one() or Decimal("0.00")
    refund_debits = connection.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.folio_id == target.id,
            LedgerEntry.account == "Guest Receivables",
            LedgerEntry.direction == "debit",
            FinancialTransaction.status == "posted",
            FinancialTransaction.transaction_type == "payment_refund",
        )
    ).scalar_one() or Decimal("0.00")
    actual_total = money(max(Decimal("0.00"), Decimal(debit_total) - Decimal(credit_total) + Decimal(settlement_credits) - Decimal(refund_debits)))
    if money(snapshot_expected) != actual_total:
        raise HTTPException(
            status_code=409,
            detail=(
                "Folio financial integrity mismatch; closure blocked. "
                f"Expected active charges {money(snapshot_expected)}, authoritative ledger total {actual_total}."
            ),
        )
