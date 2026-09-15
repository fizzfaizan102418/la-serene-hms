"""Read-only diagnostic for Folio #1 financial/reversal history.

Run from apps/api so the normal HMS_DATABASE_URL configuration is used.
This script NEVER writes to the database and NEVER prints the database URL.
"""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy import select

from app.db import SessionLocal
from app.models import FinancialTransaction, Folio, FolioItem, LedgerEntry, Reservation, Guest

FOLIO_ID = 1


def money(value) -> str:
    return f"{Decimal(str(value)):.2f}"


def main() -> None:
    db = SessionLocal()
    try:
        folio = db.get(Folio, FOLIO_ID)
        if folio is None:
            print(f"Folio #{FOLIO_ID}: NOT FOUND")
            return

        reservation = db.get(Reservation, folio.reservation_id)
        guest = db.get(Guest, reservation.guest_id) if reservation else None

        print("=" * 72)
        print(f"FOLIO #{FOLIO_ID} READ-ONLY FINANCIAL DIAGNOSTIC")
        print("=" * 72)
        print(f"folio_status={folio.status}")
        print(f"reservation_id={folio.reservation_id}")
        print(f"guest={guest.full_name if guest else 'UNKNOWN'}")
        print(f"reservation_status={reservation.status if reservation else 'UNKNOWN'}")
        print()

        items = db.scalars(
            select(FolioItem)
            .where(FolioItem.folio_id == FOLIO_ID)
            .order_by(FolioItem.id)
        ).all()

        print("FOLIO ITEMS")
        print("-" * 72)
        if not items:
            print("NONE")
        for item in items:
            gross = Decimal(item.quantity) * Decimal(item.unit_price)
            net = gross - Decimal(item.discount)
            print(
                f"item_id={item.id} | stay_id={item.stay_id} | "
                f"category={item.category!r} | description={item.description!r} | "
                f"qty={item.quantity} | unit_price={money(item.unit_price)} | "
                f"discount={money(item.discount)} | line_total={money(net)}"
            )

        print()
        print("FINANCIAL TRANSACTIONS")
        print("-" * 72)
        transactions = db.scalars(
            select(FinancialTransaction)
            .where(FinancialTransaction.folio_id == FOLIO_ID)
            .order_by(FinancialTransaction.id)
        ).all()

        if not transactions:
            print("NONE")

        for tx in transactions:
            print(
                f"tx_id={tx.id} | type={tx.transaction_type} | status={tx.status} | "
                f"ref={tx.reference_type}:{tx.reference_id} | "
                f"reversal_of_id={tx.reversal_of_id} | "
                f"item_ref={tx.reference_id if tx.reference_type in {'folio_item','folio_item_discount','folio_item_service_charge'} else '-'} | "
                f"description={tx.description!r}"
            )
            entries = db.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.transaction_id == tx.id)
                .order_by(LedgerEntry.id)
            ).all()
            for entry in entries:
                print(
                    f"    entry_id={entry.id} | account={entry.account} | "
                    f"direction={entry.direction} | amount={money(entry.amount)} | "
                    f"stay_id={entry.stay_id} | folio_id={entry.folio_id}"
                )

        print()
        print("AUTHORITATIVE GUEST RECEIVABLES SUMMARY")
        print("-" * 72)
        posted_entries = db.scalars(
            select(LedgerEntry)
            .join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id)
            .where(
                LedgerEntry.folio_id == FOLIO_ID,
                LedgerEntry.account == "Guest Receivables",
                FinancialTransaction.status == "posted",
            )
            .order_by(LedgerEntry.id)
        ).all()
        debit = sum((Decimal(e.amount) for e in posted_entries if e.direction == "debit"), Decimal("0.00"))
        credit = sum((Decimal(e.amount) for e in posted_entries if e.direction == "credit"), Decimal("0.00"))
        print(f"posted_guest_receivables_debit={money(debit)}")
        print(f"posted_guest_receivables_credit={money(credit)}")
        print(f"authoritative_balance={money(max(Decimal('0.00'), debit-credit))}")

        print()
        print("REVERSAL LINKS")
        print("-" * 72)
        reversals = [tx for tx in transactions if tx.reversal_of_id is not None]
        if not reversals:
            print("NONE")
        for tx in reversals:
            print(f"reversal_tx_id={tx.id} -> original_tx_id={tx.reversal_of_id} | status={tx.status}")

        print()
        print("NO DATABASE CHANGES WERE MADE.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
